# Depth bot runtime handoff

## Implemented

- Added `factory/scripts/depth_bot.py` with an offline `replay --day D` path that reads stored scored states and SIP quote captures, then calls the frozen adaptive-depth `replay_day` / execution pricing path directly for exact research-rule behavior.
- Replay journals per-tick summaries, all signals, funded attempts with intent and arrival quote details, order facts, exits, full sim records and a daily summary as JSONL under `data/forward/depth_bot/journal/`.
- Added shared control primitives for the dedicated `data/forward/depth_bot/ARMED` path, `data/KILL`, daily realized-loss stop, 15:55 ET entry cutoff/flatten clock, and journal/broker-symbol reconciliation. Paper credentials are loaded only from repo `.env`; paper client creation is guarded by both `--paper` and ARMED.
- Added `tests/test_depth_bot.py` covering the armed gate, KILL entry-vs-exit behavior, daily loss stop, flatten cutoff, restart adoption, unarmed paper fail-closed behavior, and four stored-day parity comparisons (2023-01-19, 2023-01-24, 2025-12-30, 2025-12-31).

## Important limitation / not ready to arm

The Alpaca SIP stream, full-PIT universe ranking/admission capture and live feature-to-order loop are not implemented here. `shadow` and `paper` therefore fail closed after control/reconciliation rather than pretending to run a live strategy. The paper broker code reconciles positions/orders but does not submit entries or exits. Required remaining work before arming: implement the actual SIP capture and full-universe ranking integration; consume `IncrementalDepthFeatures` on the common 5-minute lattice; apply live `DepthBook`/risk accounting; submit IOC buys and DAY market sells through Alpaca; implement quote/bar persistence and restart restoration; then verify the requested four-day replay and behavioral tests in the main agent.

No parity/test/ruff commands were run here. End-to-end parity values are therefore unobserved and intentionally not claimed.
