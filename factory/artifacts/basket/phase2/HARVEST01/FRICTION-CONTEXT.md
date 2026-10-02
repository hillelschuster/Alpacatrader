# HARVEST01 — FRICTION-CONTEXT

**What local data can turn the 100 bps assumption into a measured number.**

Date: 2026-10-02. Read-only scouting pass; no repo files were touched, no network acquisition,
no code executed against the tape. (Main agent materialized this file verbatim from the scout.)

---

## 0. How to read this file — provenance labels

| Label | Meaning |
|---|---|
| **MEASURED (this pass)** | The scout opened the file named and read the values below. |
| **QUOTED (prior artifact)** | The number already exists in a committed artifact; the artifact was read, not the raw tape. |
| **NOT MEASURED** | Requires reading parquet rows and/or running polars — specified as exact commands (§6). |
| **[INFERENCE]** | Derived by reasoning from read files; flagged wherever load-bearing. |

**Deliberate omission:** no quoted-spread number, no impact number, and no quotes-coverage
percentage for the HARVEST01 population is stated here. Those are the three things the next move
needs and none is readable without executing the reader in §6.

---

## 1. The 100 bps ruler as currently implemented (what it actually prices)

Source: `factory/scripts/basket_harvest_sim.py:14-18, 43-48, 152-153` and
`factory/artifacts/basket/phase2/HARVEST01/AUDIT-CAUSALITY.md` item 9.

```python
FEE  = {100: 0.005, 150: 0.0075}          # basket_harvest_sim.py:47
SIDE = {100: FEE[100], 150: FEE[150]}     # basket_harvest_sim.py:48
row[key + "_100"] = (ex_px * (1 - SIDE[100])) / (fpx * (1 + SIDE[100])) - 1
```

| Label | per-side | round-trip wealth drag at an unchanged price |
|---|---|---|
| `100` bps | 0.005 | `(1-0.005)/(1+0.005) = 0.990050` → **99.50 bps** |
| `150` bps | 0.0075 | `(1-0.0075)/(1+0.0075) = 0.985112` → **148.89 bps** |

What the ruler does **not** price: no spread crossing (flat haircut on the execution price, not a
half-spread); no size (independent of shares/notional/minute volume); no source reconciliation
(applied to a provider 1-minute bar open — `basket_harvest_bars.py:63-100`, `feed=SIP`,
`adjustment=RAW` — never compared with the raw print that actually happened); no broker fee
schedule (0.005/0.0075 are project assumptions).

> **100 bps today = a flat, size-blind, print-unaware multiplier on a provider bar open.**
> Every one of those three blindnesses is locally measurable (§5).

---

## 2. `data/sip/net/quotes` — schema, coverage, resolution

### 2.1 Exact schema (identical on every day manifest opened)

| # | column | polars dtype | notes |
|---|---|---|---|
| 1 | `symbol` | `String` | ticker |
| 2 | `ts_utc` | `Datetime(time_unit='us', time_zone='UTC')` | **microsecond**; tz-aware UTC |
| 3 | `bid_price` | `Float64` | |
| 4 | `ask_price` | `Float64` | |
| 5 | `bid_size` | `Float64` | shares |
| 6 | `ask_size` | `Float64` | shares |
| 7 | `bid_exchange` | `String` | single venue id, not a routed list |
| 8 | `ask_exchange` | `String` | |
| 9 | `conditions` | `List(String)` | **quote-side** condition codes — do not reuse the trade-side table |
| 10 | `tape` | `String` | |

**Resolution: tick, not minute.** No minute aggregate on disk. Bid/ask **and sizes** exist at
**top of book only** (one level; no depth ladder).

### 2.2 Concrete reads (MEASURED this pass)

- quotes/2021-02-01: 17 symbols requested/with_data, 1,462,943 rows, window 09:25–16:05 ET,
  `sha256 f18487108693e366cff5bc2cf23e50e3938e07ab73ec439e0169375a6ca8eefa`.
- trades/2021-02-01: 67 symbols, 4,365,050 rows, `sha256 d6aedbdeb8b592b53d22f7a08e616271162a2b1101df98ad17cefe88792be535`.
- quotes/2026-05-29: 17 symbols, 853,019 rows; trades/2026-05-29: 61 symbols, 5,060,650 rows.
- Also read: 2021-01-29 (13 syms quotes / 71 trades), 2021-05-27 (13 / 84).

### 2.3 Coverage

| quantity | quotes | trades |
|---|---|---|
| files in dir | ~1,068 day pairs | ~1,068 day pairs |
| first / last day present | 2021-01-29 … 2026-05-29 | same |
| `2024-*` days | absent | absent |
| `2025-01-*` days | absent | absent |
| ingestion window (ET) | `09:25-16:05` | same |
| symbols/day | 13–17 | 61–84 |
| rows/day | 0.85–3.93 M | 2.76–8.31 M |
| total bytes | not measured | **23,829,091,862 B over 1,066 files** (QUOTED — `ATLAS/E3/census.json`) |

Day-level coverage vs the HARVEST01 dev calendar: effectively 1,066/1,066 [INFERENCE, boundary
spot-checks only; §6.1 proves it exhaustively in ~30 s].

### 2.4 The quotes universe is *narrow by construction* — read before planning coverage

`factory/scripts/sip_ingest.py:94-113`: `trades = net` (~61–84 symbols/day);
`quotes = union of the top-3 of each candidate snapshot` (~13–17 symbols/day). The ranking behind
those top-3 lists is the ATLAS candidate score, **not** HARVEST01's market-wide
gain-vs-previous-close ranking. The populations overlap only partially, by construction (§4.2).

### 2.5 Computing the quoted spread at minute C for symbol S — feasible, conditionally

Feasible for any symbol in that day's quotes file; the blocker is *membership*, not mechanics.
Required hygiene: drop zeroed/crossed quotes; weight by time-to-next-change (event-driven ticks);
never reuse `sip_bars.RULES_M` (trade-side) on quote conditions until the quote vocabulary is
verified; convert `ts_utc`→ET per row (2021-02-01 sits in EST, 2026-05-29 in EDT).

---

## 3. `data/sip/net/trades` — schema recap and the three recipes

### 3.1 Exact schema

`symbol, ts_utc (us, UTC, ascending per symbol), price, size, exchange, conditions (List),
trade_id (Int64), tape`. Reusable **price-updating filter**:
`basket_t5_rawpaths.price_updating` (joins `sip_bars.combine(conds, tape)`, keeps `hl == 2`).
Auction/derived prints carry codes `{Q, M, O, 5, 6}` (`sip_bars.py:58`), split by
`sip_bars.auction_prints`; session partition `et < 570` pre / `570 ≤ et < 960` rth / else post.

### 3.2 (a) First-print price after minute C

Two readings — pick one and say which:
- **Sim-aligned (use this):** first price-updating print whose ET minute equals C (the next bar's
  `et` if minute C has none — the `gap >= GAP_BLOCK → blocked` case).
- Literal: first print with `et >= C+1`.

### 3.3 (b) Realized print-to-print moves

Log returns over consecutive prints; per-minute realized vol; minute range. Caveat: prints are
microsecond-stamped but not gap-free — a print-to-print move spanning a halt is a *jump*;
`basket_subminute_probe.empty_runs/minute_coverage` brackets those holes.

### 3.4 (c) Size-aware impact proxies (trades-only)

1. **Participation rate** — our notional ÷ minute-C dollar volume (and first-60 s).
   `basket_harvest_subminute.py` already emits `first60_sz`, `n_prints` for this.
2. **Amihud illiquidity per minute** — `|r_close| / Σ(price·size)`.
3. **Kyle-λ** — regress print-to-print `r_i` on signed size, over ±60 s around the fill.
4. (quotes-only, ~13–17 names) **Effective spread** `2·|trade_px − mid(ts)|`.

Existing probe: 120 rows / 79 days in `data/harvest01/report/subminute_probe.parquet`
(entry side only; `exit_px_diff` is a dead branch — fix it for exit-side slippage).

---

## 4. Coverage of the HARVEST01 populations

### 4.1 Trades coverage — QUOTED (stale; re-run §6.2 before citing)

`readouts/trades_coverage.md` (7 days): pooled distinct selected tickers 57/81 = **70.4%**;
mean of per-day shares 72.3%; rank-1 leaders 17/24 = 70.8%; **member rows 1406/1596 = 88.1%**.
Two staleness flags: the denominator (7 days) predates the finished 1,066-day selection lane,
and the artifact's claim that only 3/7 days have sim/fills is wrong on disk (19 files exist for
2021-02-*). Re-run before quoting.

### 4.2 Quotes coverage of the selected basket — **NOT MEASURED**

No quotes-coverage number exists for HARVEST01 anywhere on disk. The ATLAS figure
(`E3/latency.json → quote_present_share = 0.999838`) is **tautological for that population**
(ATLAS members are drawn from the very set that generated the quote request) and must never be
quoted as evidence here. Expected direction [INFERENCE]: strictly below the trades coverage,
because the quote set (13–17) is a strict subset of the trade net (61–84), and neither is
HARVEST01's selection.

### 4.3 The near-zero scalp cells (clock 569 → exits 571/575/580/585)

From `readouts/readout.md`: best cell 569→575 N=1 m100 −0.08% (med −1.35%, pos 0.40, B1 0.0 /
B2 −0.28, n=1,066, t=−0.3); 569→571 −0.14%; longer holds worse; `pos ≈ 0.33–0.45`; B1/B2 flip
sign; the mean is carried by a thin tail. These are 09:29→09:31/09:35 scalps entirely inside the
SIP window; **minute 569 is two minutes before the 09:30 official open** — the scalp brackets the
opening auction (§4.4.4). Coverage of these cells: **NOT MEASURED** (§6.2 covers it).

### 4.4 What the 100 bps ruler needs from these cells, in order

1. Bar-open → first-print delta at 569 and 571/575/580/585 (trades, ~70% of names).
2. Participation rate in minutes 569 and 571–585 (trades).
3. Effective spread + quoted depth on covered names (quotes, ≤17/day) — a *scale* check only.
4. **Auction-print identification** — `sip_bars.auction_prints` isolates `{Q,M,O,5,6}`; this is
   the single most discriminating check on the scalp cells (auction repricing vs tradeable drift).

---

## 5. Friction decomposition: what is computable locally

### 5.1 Computable today from trades (no quotes)

Bar-open vs first-print slippage (entry and exit); realized print-to-print moves; print density
and silence (prints/minute, longest gap, minute coverage); participation/Amihud/Kyle-λ around the
entry minute; auction vs continuous attribution; fill delay (`fill_et − clock`, blocked rule,
`first_print_et − fill_et`).

### 5.2 Computable only on quote-covered names (13–17/day)

Quoted spread at any minute (bps of mid; tick-median and time-weighted); top-of-book sizes;
effective spread per print; trade-through rate. Use as a scale check only — membership is
unrepresentative by construction.

### 5.3 NOT computable locally

Multi-level book depth; true NBBO for ~70% of selected names; market impact of our own order /
queue position / fill probability; actual broker execution and the real fee stack; short borrow;
sub-second decision→print latency (no live-feed timestamp).

### 5.4 Bottom line

> Of the friction decomposition, everything that depends on price and size is computable today
> from the trades tape for ~70% of selected names; everything that depends on the book is
> computable only for the 13–17 quote-covered names per day, by construction; and nothing that
> depends on our own order or on a real fee schedule is computable from local data at all. The
> 100 bps ruler can be replaced by a measured `spread + slippage + impact` estimate on the
> trades-covered subset, and must remain a labelled assumption on the rest.

---

## 6. Exact commands that close the gaps (read-only, bounded)

### 6.1 Day-level coverage proof (~30 s)

```python
import json, pathlib
D = pathlib.Path("/home/hillel/projects/Alpacatrader/data/sip/net")
cal = json.loads(pathlib.Path("factory/artifacts/basket/sip/phase2_session_calendar.json").read_text())["evidence"]
dev = set(cal)
for kind in ("trades", "quotes"):
    have = {p.stem for p in (D/kind).glob("*.parquet")}
    print(kind, "files:", len(have), "dev days covered:", len(dev & have), "/", len(dev),
          "missing:", sorted(dev - have)[:5], "extra:", sorted(have - dev)[:5])
```

### 6.2 Member-day quotes + trades coverage for both populations (one pass)

Populations: (i) every distinct selected ticker over dev days
(`base/selected/<day>.parquet`); (ii) the near-zero scalp cells only, derived from
`sim/fills/<day>.parquet` (`variant == "primary"`, `clock == 569`, any `r571_100 / r575_100 /
r580_100 / r585_100` non-null). Full script body in the scout transcript; the loop reads only
the `symbol` column of each day's trades/quotes file and prints ticker-level and day-level
coverage for both populations.

### 6.3 Render the sub-minute probe statistics already on disk

`subminute_probe.parquet` (120 rows / 79 days) already carries `entry_px_diff`, `first60_n`,
`first60_sz`, `first60_range`, `max_print_gap_min`; the markdown summary only has its header.
One read prints the missing table (median entry print-vs-fill, first-60 s prints/shares, max gap
by outcome class).

### 6.4 Row-level reads (few rows)

Quotes 2026-05-29 minute 569 (17 symbols); trades 2021-02-01 minute 569 for LODE/ALYA/ASM/VIE
via `basket_subminute_probe.load_prints` + `basket_t5_rawpaths.price_updating`.

---

## 7. Caveats a reader must carry forward

1. This pass used `read`/`grep`/`glob` only (the judge/find backend returned HTTP 402); no file
   was executed, so §6 holds commands, not numbers.
2. `readouts/trades_coverage.md` is stale in two ways (§4.1) — re-run §6.2 before citing.
3. The 99.98% quote coverage in `ATLAS/E3/latency.json` is tautological for ATLAS and must never
   be quoted as evidence for HARVEST01.
4. Day coverage is an [INFERENCE]; §6.1 makes it exact.
5. Quotes `conditions` semantics are unverified — do not filter quotes with the trade-side table.
6. **Auction contamination**: minute 569 lies two minutes before the official open; until §4.4.4
   is run, "near-zero" and "auction artefact" are not distinguishable.
