# RUNTIME_DESIGN — execution, state, recovery, VPS for the v0 paper bot

Companion to CANONICAL_STRATEGY.md (frozen spec), BOT_DATA_MAP.md, DATA_PATH_DESIGN.md.
Strategy frozen — this doc decides runtime architecture only.

## 1. src/ v0.5.0 reuse audit (read-only inspection, evidence file:line)

| module | verdict | evidence / reason |
|---|---|---|
| `_atomic.py` | **REUSE-AS-IS** | clean atomic JSON swap (fsync + os.replace) — exactly what state files need |
| `trade_ledger.py` | **REUSE-AS-IS** | 19-line JSONL appender; ledger schema from BOT_DATA_MAP §6 |
| `state_machine.py` | **REUSE-AS-IS** (subset) | `PositionStore`/`PendingOrderStore` with atomic save/load (`state_machine.py:168-252`); transition guards; drop old candidate-stage helpers |
| `paper_execution.py` | **ADAPT** | keep `_call_with_timeout` (:108), `get_alpaca_market_session` (:142, calendar/clock + holiday fallback), `AlpacaExecutionGateway` order lifecycle (:703-1378) and `reconcile_positions`/`reconcile_open_orders` (:1380-1519); strip stop-loss/add-to-runner paths the canonical strategy never uses (fixed 60m exit, no stops) |
| `models/schemas.py` | **ADAPT** | keep `PositionStateModel`, `PendingOrder`, `EntrySignal` skeleton; drop `AttentionScore`/`HardFilterResult`/`EntrySetupType` (old strategy) |
| `market_data.py` | **REPLACE** | built for per-candidate snapshots + `IEX_SCALE=40` + EMA9/prior_hod (old strategy); canonical needs the websocket ring-buffer engine (DATA_PATH_DESIGN §3) |
| `scanner/` | **IGNORE** | Finviz scrape pipeline; rank population must be the onboarded universe (CANONICAL §C) |
| `decision_pipeline.py` | **IGNORE** | old candidate→snapshot→filter flow; superseded by minute-loop |
| `entries.py` / `exits.py` | **IGNORE** | old setup detection (ATR, runner promotion, stops); canonical = M3/composite/E6 + fixed 60m |
| `hard_filters.py` | **IGNORE** | old filter stack; canonical gates are rvol/vwap_dist/tod/score only |
| `move_classifier.py` / `classifier_features.py` | **IGNORE** | superseded by frozen `model_v1.pkl` + the 30 canonical features |
| `runner.py` | **IGNORE** | runner-promotion logic (`compute_atr`, `should_promote_to_runner`) — no equivalent in canonical |
| `app.py` / `analytics.py` / `sizing.py` / `market_data_sim.py` | **IGNORE** | old orchestrator/analytics; v0 sizing is fixed $10k units |
| `journal/decision_logger.py` | **ADAPT (optional)** | logging pattern reusable for the per-minute admission log |

**Build path: a new lean runner module (`src/topgain_bot.py` + `src/data_engine.py`)
composing the reusable primitives.** Not extending `runner.py` — its control flow is the
old strategy's. The new code is ~500–800 lines: websocket consumer, minute loop,
feature computation (port of BOT_DATA_MAP §4), scoring, order calls into the adapted
gateway, ledger writes. **Do not rewrite the order lifecycle** — `AlpacaExecutionGateway`
already handles submit/confirm/cancel/reconcile against the Alpaca paper API.

## 2. Exact minute loop (timeline, research-parity vs actual fills)

```
T-1s         websocket minute bar for [T-60s, T) arrives (~T)
T+2s         updatedBars folded in (last 2 min)
T+5s         grid row built; rank; features; scores for top-20 + open positions
             decision: M3 (score≥0.00115 ∧ vis_rank≤2) ∧ composite (rvol>4 ∧
             vwap_dist>0.03 ∧ tod<270) ∧ E6 (rvol>8 ∧ tod≤328 ∧ cum_dv≥$5M)
T+5..8s      ENTRY: submit paper market order immediately (AlpacaExecutionGateway)
             → per-trade ledger record created with model fill left null
T+65s        t+1 bar closed → model_fill = close(t+1) recorded (REST/websocket),
             never blocked the order for it
fill stream  order-fill event → actual_fill price/timestamp recorded;
             fill_slippage = actual − model_fill (backfilled when t+1 lands)
T+61min      EXIT decision minute: submit paper market exit order at t+61+5s
             model_exit = close(t+61) recorded after that bar closes
             actual exit from fill stream; exit_slippage likewise
re-entry     admission ≥ p_prev + 61 (canonical E6); 1 position/ticker; cap 10
```

- Research-parity pricing (`model_fill/model_exit`, the +20bps convention) is computed
  from bars **after the fact** and never delays an order.
- Position sizing fixed: $10k units (qty = 10000/price), max 10 concurrent.
- Entry cutoff tod_min ≤ 328; no session-time gate otherwise; EOD force-flat NOT
  required for paper (last entry 14:58 + 60m ≤ 15:58) — but a 15:58 reconciliation
  sweep logs any position still open (halts) without closing it silently.

## 3. State and recovery

Persisted (all via `_atomic.write_json_atomically` or JSONL append):

| state | file | written |
|---|---|---|
| positions | `journal/positions.json` (`PositionStore.save_to_disk`) | on every transition |
| pending orders | `journal/pending_orders.json` | on submit/confirm |
| per-trade + per-minute ledger | `journal/ledger_YYYYMMDD.jsonl` | append-only |
| rvol baseline | `journal/rvol_baseline.jsonl` | daily 16:05 append |
| daily rollup | `journal/rollup_YYYYMMDD.json` | 16:10 |

**Restart procedure**:
1. `PositionStore.load_from_disk`; cross-check against Alpaca paper
   `get_all_positions()` — any mismatch is reconciled by `reconcile_positions`
   (existing, `paper_execution.py:1380`) and logged loudly.
2. Rebuild today's bars: REST backfill ~100 min (or session start) for tracked symbols;
   prev_close/session_open from snapshots; cum_dv recomputed from backfilled bars.
3. Idempotency: `client_order_id` = `f"tg{et_date}{tod_min}{ticker}"` — a crash between
   order submit and ledger write cannot double-enter (same id is rejected/looked-up);
   one-position-per-ticker enforced by `PositionStore.locked_symbols()`.
4. Missed exit (reboot past hold_until): submit exit immediately at boot; model_exit
   recorded from REST close(hold_until) if intraday, else flagged
   `exit_recovered_after_restart` (rare; disclose in rollup).

## 4. VPS and region

- **Region: us-east-1 (N. Virginia).** Alpaca's infrastructure is there; 1–5 ms to data
  + paper matching engine vs ~110 ms from eu-north-1. Latency is economically
  irrelevant for minute decisions + 60m holds — the reason for us-east-1 is data-feed
  stability (websocket drops/reconnects) and ops simplicity, not edge.
- **Separate instance from the Stockholm Polymarket box: yes.** Clean blast radius,
  independent restarts/deploys, no resource contention, different market-hours
  monitoring. Leave the Polymarket server untouched.
- **Sizing: `t4g.medium` (2 vCPU ARM, 4 GB, ~$24/mo)** — websocket consumer (~5% core),
  ring buffers <100 MB, polars scoring, JSONL I/O. Storage 20 GB gp3 (ledgers are
  ~1–5 MB/day; years of headroom). Ubuntu 24.04, python 3.12 + uv. No docker/k8s —
  systemd service + logrotate is enough at this scale.

## 5. Ops

- `topgain-bot.service`: `ExecStart=uv run python -m src.topgain_bot`, `Restart=always`,
  `EnvironmentFile=.env` (ALPACA keys + data plan), `WantedBy=multi-user.target`.
- Timezone: host UTC; all session logic in `America/New_York` via `zoneinfo` (already
  the pattern in `paper_execution.py`).
- Heartbeat: `journal/heartbeat.json` every minute during RTH; external check (uptime
  robot or cron on the Polymarket box) alerts if stale >5 min during RTH.
- Daily 16:10 rollup (BOT_DATA_MAP §6) is the paper-gate input
  (+30bps/trade over ≥20 trading days).
- Cost: ~$24/mo VPS + $99/mo Algo Trader Plus.

## Open blockers

- Algo Trader Plus subscription active on the Alpaca account (websocket stream +
  REST history for boot-time baseline pre-warm; paper trading itself is free).
- `src/` write permission for the two new modules + adapted gateway wiring (per
  two-tracks rule — pending explicit user go).
