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

## Bar builder (derived layer) — `factory/scripts/sip_bars.py`

Rebuilds 1-minute bars from the raw trades using Alpaca's **documented** bar-aggregation
table (Market Data FAQ, "How are bars aggregated?"): per-condition open/close, high/low
and volume update rules, tape-conditional rows, strictest-rule-wins for multi-condition
trades, minute = timestamp truncated to the minute (NY), bar emitted only when price
fields exist. Auction/official codes (`Q, M, O, 5, 6`) update nothing in bars and are
written separately (`auction_prints_*.parquet`; 221 prints on the first pilot day).

First certification against the provider (2021-02-01, heaviest-era day):

- 3,318,734 trades -> **16,613 bars** across 54 symbols (~20 s).
- vs provider SIP 1-minute bars: **16,613/16,613 matched, 0 ours-only, 0 provider-only
  inside the 09:25-16:05 window** (all 6,675 provider-only bars are outside the window),
  **99.982% exact cells** (16,610/16,613).
- 3 residual mismatches, all **volume-only, OHLC identical**:
  - `STPK 11:28` ours +4,000 sh = a `[' ','B']` Average Price block the provider excludes
    from volume (its documented table says volume updates for `B`) ;
  - `ALYA 11:46` ours +1 trade / +50 sh and `STPK 09:30` ours +3 trades / +120 sh —
    regular-family prints present in the final consolidated tape but absent from the
    provider's as-published bars (late/superseded-print semantics).
- Interpretation: our builder replicates the provider bar *content* almost exactly;
  the tiny residual is tape-finalization and avg-price-volume policy, not bad prints.
  OHLC (what ranking and path measurement use) is unaffected in this sample; the
  certification panel must confirm per-day. Impact on ranking/MFE/MAE is measured
  in the pilot comparison, not assumed.

## Certification layer — `factory/scripts/sip_certify.py`

Compares SIP-derived facts against the committed legacy anatomy, per day: re-rank of the
stored top-10 by SIP prices, fill/MFE/MAE/path deltas, first-passage ordering (including
resolution of stored AMBIGUOUS cases by trade sequencing; raw and price-updating lenses),
quote/spread at fill, and RTH price-updating max vs stored day high. Outputs:
`factory/artifacts/basket/sip/certification_<day>.json`, `certification_panel.json`.
`--day D --why TICKER` prints legacy-vs-SIP bars around a member's fill for review.

Day-1 (2021-02-01) result and verified disagreement mechanisms — legacy missing minutes,
single-print minutes, and ranges crossing rulers — are documented in `CERTIFICATION_NOTES.md`.
Headline: fill deltas to 469bps, MFE deltas to 492bps, 21 non-ambiguous order flips across
4 members, quoted spread at fill p50 ~107bps / p90 ~358bps, 10 symbol tail diffs to +9.3%.

## Not in this layer

- No bar construction, no price series, no trade-condition policy (derived layer).
- No quote-based fills or execution model (separate layer; market truth ≠ assumed fill).
- No LULD status: Alpaca does not expose halt flags here; halts remain inferred
  from the event stream (no-trade intervals) in derived artifacts.
