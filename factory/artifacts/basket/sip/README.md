# SIP canonical raw layer (BASKET-01 data upgrade)

Per user directive: raw market record first, explicit interpretation second. This
layer holds Alpaca **SIP trades + quotes** exactly as the provider delivers them —
no cleaning, no condition filtering, no resampling. Interpretation (bar construction,
accepted-trade policies, execution models) belongs in derived layers on top.

## What exists

- Producer: `factory/scripts/sip_ingest.py` (self-tested).
- Raw storage (gitignored, local): `data/sip/trades/YYYY-MM-DD.parquet`,
  `data/sip/quotes/YYYY-MM-DD.parquet` — atomic writes (`.tmp` + `os.replace`).
- Per-artifact manifests: `data/sip/<kind>/YYYY-MM-DD.manifest.json`; append-only
  global log `data/sip/manifest.jsonl`. Sample manifests are committed here
  (`MANIFEST_SAMPLE_*.json`) because `data/` is not in git.
- Schema (identical names in both parquet and manifest `schema`):
  trades = `symbol, ts_utc (µs, tz UTC), price, size, exchange, conditions[list], trade_id, tape`;
  quotes = `symbol, ts_utc, bid_price, bid_size, ask_price, ask_size, bid_exchange, ask_exchange, conditions[list], tape`.

## Semantics

- Daily window: **09:25:00–16:05:00 America/New_York** (DST-correct via zoneinfo;
  EST start = 14:25 UTC, EDT start = 13:25 UTC). Requested window is recorded in
  the manifest (`window_utc`, `window_et`).
- Pilot symbol sets: trades = union of the day's anatomy snapshot names (top-10 per
  snapshot) + winners; quotes = union of top-3-per-snapshot names. Names ranked 11+
  by SIP are NOT fetched in the pilot (revisit after certification).
- Status/resume: `ok` + parquet present ⇒ skipped on rerun; `partial` (per-symbol
  errors recorded) and `probe` (`--limit-symbols`) always retry; `--force` overrides.
- `size` is Float64 (blocks can exceed int32 in the raw feed); `trade_id`/`tape`
  preserved as delivered.

## Measured pilot facts (2021-02-01, heaviest-era day)

- trades: 3,318,734 rows / 54 symbols / 29.5 MB parquet / 264 s.
- quotes: 959,117 rows / 15 symbols / 5.1 MB parquet / 85 s.
- ts coverage 09:25:00.205851–16:04:59.974 ET; no nulls; 16 exchanges;
  condition codes present (e.g. `['@']` regular, `['@','I']` odd lot,
  `[' ']`, `['@','F']` sweep) — to be interpreted ONLY in derived layers.

## Not in this layer

- No bar construction, no price series, no trade-condition policy (derived layer).
- No quote-based fills or execution model (separate layer; market truth ≠ assumed fill).
- No LULD status: Alpaca does not expose halt flags here; halts remain inferred
  from the event stream (no-trade intervals) in derived artifacts.
