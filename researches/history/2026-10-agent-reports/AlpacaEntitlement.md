# Alpaca Entitlement Probe — 2026-10-27 (read-only, no secrets)

Probe time: Tue 2026-10-27 08:51 ET (12:51 UTC), premarket; regular session open 09:30 ET (13:30 UTC), close 16:00 ET.

## Credentials present (names only)
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` — exactly ONE key set. No separate live-vs-paper key pair exists.
- `ALPACA_PAPER=true`, `ALPACA_BASE_URL=https://paper-api.alpaca.markets`, `ALPACA_DATA_URL=https://data.alpaca.markets`, `TRADING_MODE=paper`.
- `TRADING_LIVE_TRADING_CONFIRMED` present.

## (1) Trading auth per endpoint, same key set
| Endpoint | Result |
|---|---|
| paper-api.alpaca.markets | OK — status ACTIVE, trading_blocked False, acct PA3I949NRKSR, currency USD, buying power ~4e5 (hundreds of thousands), positions readable, options trading level 3 |
| api.alpaca.markets (live) | FAIL 40110000 "request is not authorized" |

`pattern_day_trader` is None/unset (paper accounts are not PDT-scored; PDT is irrelevant to shadow day).
Conclusion: keys are paper-only. Live trading is NOT enabled — no live plan purchased/approved.

## (2) Market data
| Request | SIP | IEX |
|---|---|---|
| latest quote AAPL | OK, real-time (ts == probe time) | OK but STALE (prev session 20:00 UTC) |
| latest trade AAPL | OK real-time | OK stale |
| 1-min bars near now | OK, bar at 12:50 UTC while clock 12:51 UTC = real-time | 0 rows (no premarket IEX prints) |
| historical, 30-min-old window | OK | OK (5 rows) |
| 2016 daily bars | OK (5 rows) | 0 rows |
| snapshot | OK via REST | n/a |

No 403/subscription errors on SIP anywhere. Real-time SIP is fully entitled for REST.

## (3) Streaming (6-10 s session each, closed cleanly)
- `StockDataStream(feed=SIP)`: auth OK, no subscription error, 39 msgs in 9 s premarket (AAPL/MSFT/SPY trades), prices match REST. Real-time SIP streaming WORKS.
- `StockDataStream(feed=IEX)`: connects with no error, 0 msgs (premarket IEX inactivity, not an auth failure).

## (4) Symbol limits
- No limits endpoint exists (`/v2/stocks/meta/limits` -> 404); Alpaca does not report a subscription symbol cap via API.
- Empirical: 50-symbol REST latest-quote batch OK; 72-symbol SIP websocket subscription accepted, 1010 quotes in 8 s, no error. No cap hit; Algo Trader Plus-class streaming is effectively uncapped.

## Extras found
- News API (`/v1beta1/news`): 200 OK.
- Options snapshots (`/v1beta1/options/snapshots/SPY`): 200 OK (level 3 options trading on paper account).
- Implied plan: Algo Trader Plus ($99/mo) or higher — real-time SIP REST + streaming + news + options.

## Risk finding for shadow day
`src/market_data.py` hardcodes `DataFeed.IEX` in every request (lines 77, 265, 278, 292, 375, 406, 417, 463) — snapshots, quotes, bars. IEX is a single venue (~2% of volume), returns zero bars premarket, and gives thin/sparse prints for low-float movers; the account already pays for real-time SIP. Shadow-day measurements taken on IEX will understate volume/RVOL and miss prints. Recommend switching to `DataFeed.SIP` (optionally env-driven with IEX fallback) before the shadow day.

## What the owner must buy/enable
Nothing — all required capabilities are already active: paper trading auth, real-time SIP REST, real-time SIP streaming, news, options. No purchase needed for the shadow day.
