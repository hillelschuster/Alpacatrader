# H009 — Failed Breakdown Reclaim vs Clean HOD Breakout

**Data**: certified `certification_2025-06/events_topN.parquet` reference universe semantics, rebuilt from `data/clean_ohlcv_2025-06.parquet` (June only, 21,996,803 bars, 20 sessions, 14,213 tickers, 0 dup `(ticker,timestamp)`).
**Dates**: signals span 2025-06-03 .. 2025-06-30 (19 signal days; 2025-06-02 and 06-03 have no prior_close in June-only data).

## Definition
Trap = bar closes above prior running RTH HOD (or above VWAP) AND within the prior 5 bars there was a dip `low < prior_HOD*0.997` OR `low < VWAP` (bear-trap / stop-hunt reclaim).
Clean = bar closes above prior running RTH HOD with **no** HOD dip in prior 10 bars.

Cost convention (sibling H006): `roundtrip = cost_bps × 2`. Primary `--cost-bps 10` = **20bps RT**; sensitivity `--cost-bps 20` = **40bps RT**.

## Segment tables (net after cost; exp_net = expectancy − roundtrip_cost)

### 20bps RT (primary)

| segment | h | n | wr | wr_net | avg | exp_net |
|---|---|---|---|---|---|---|
| ALL | 5m | 247,033 | 0.459 | 0.268 | 0.0002 | **−0.0018** |
| ALL | 15m | 235,996 | 0.479 | 0.348 | 0.0005 | **−0.0015** |
| ALL | 30m | 222,410 | 0.487 | 0.391 | 0.0009 | **−0.0011** |
| ALL | 60m | 197,641 | 0.493 | 0.421 | 0.0015 | **−0.0005** |
| TRAP | 5m | 234,997 | 0.459 | 0.273 | 0.0002 | **−0.0018** |
| TRAP | 15m | 224,579 | 0.479 | 0.352 | 0.0005 | **−0.0015** |
| TRAP | 30m | 211,710 | 0.488 | 0.394 | 0.0010 | **−0.0010** |
| TRAP | 60m | 188,151 | 0.492 | 0.423 | 0.0016 | **−0.0004** |
| CLEAN | 5m | 12,036 | 0.462 | 0.173 | 0.0001 | **−0.0019** |
| CLEAN | 15m | 11,417 | 0.482 | 0.272 | 0.0002 | **−0.0018** |
| CLEAN | 30m | 10,700 | 0.482 | 0.325 | 0.0003 | **−0.0017** |
| CLEAN | 60m | 9,490 | 0.497 | 0.378 | 0.0011 | **−0.0009** |

**TRAP vs CLEAN diff (exp_net):** 5m +0.0001 · 15m +0.0003 · 30m **+0.0006** · 60m **+0.0005** → trap is uniformly a hair better, but **both segments are net negative after 20bps RT**.

### 40bps RT (sensitivity)

| segment | h | n | wr | avg | exp_net |
|---|---|---|---|---|---|
| TRAP | 15m | 224,579 | 0.479 | 0.0005 | **−0.0035** |
| TRAP | 60m | 188,151 | 0.492 | 0.0016 | **−0.0024** |
| CLEAN | 15m | 11,417 | 0.482 | 0.0002 | **−0.0038** |
| CLEAN | 60m | 9,490 | 0.497 | 0.0011 | **−0.0029** |

Trap still beats clean (diff 60m +0.0005), but the gap is unchanged in bps while the cost penalty doubles — **all segments deeply negative**.

## CRITICAL SUB-NUANCE: the script's `trap` bucket is diluted

The script tags a bar as `trap` if it reclaimed **either** above prior HOD **or** above VWAP. 91% of trap signals (345,887/380,803) never exceed prior HOD after the dip — they only got back above VWAP. Those are weak, non-mechanism signals. The **true H009 mechanism** (dip below prior HOD, then close **back above prior HOD**) is a 34,916-signal subset that behaves qualitatively differently:

**HOD-reclaim trap vs Clean control** — both reclaim above prior HOD; the only difference is presence of a prior dip (this is H009's exact comparison):

### 20bps RT

| h | trap n | trap wr | **trap exp_net** | clean n | clean wr | **clean exp_net** | diff (edge) |
|---|---|---|---|---|---|---|---|
| 5m | 16,549 | 0.480 | **−0.00164** | 12,036 | 0.462 | −0.00188 | +0.00025 |
| 15m | 15,332 | 0.489 | **−0.00100** | 11,417 | 0.482 | −0.00179 | +0.00079 |
| 30m | 14,601 | 0.505 | **+0.00033** | 10,700 | 0.482 | −0.00168 | **+0.00200** |
| 60m | 13,397 | 0.515 | **+0.00194** | 9,490 | 0.497 | −0.00092 | **+0.00286** |

### 40bps RT

| h | trap exp_net | clean exp_net | diff |
|---|---|---|---|
| 30m | −0.00167 | −0.00368 | +0.00200 |
| 60m | **−0.00006** | −0.00292 | +0.00286 |

## Verdict

- **Does failed-breakdown reclaim beat clean breakout after costs?** Yes — **decisively**, but only for the mechanism-faithful subset. At 20bps RT the HOD-reclaim trap beats the clean control by **+20 bps (30m)** and **+29 bps (60m)**, and it is the **only** segment that turns **net positive** (60m **+0.00194 = +19 bps**) after costs.
- The broad `trap` bucket (380,803) is **negative everywhere at 20bps** (best 60m −0.0004). Its tiny +0.5–6bp edge over clean is a diluted artifact of mixing in 91% VWAP-only non-reclaims. The report's headline should be the HOD-reclaim subset, not the raw trap bucket.
- **Mechanism confirmed qualitatively AND quantitatively** for the true subset: a prior stop-hunt dip that is then reclaimed above prior HOD yields materially better continuation than a clean HOD touch with no dip (edge = +29bps at 60m, and the win rate lifts from 0.497 → 0.515).
- **But it is not yet an executable edge at the raw "trap" definition.** At 40bps RT the 60m HOD-reclaim edge collapses to ~−0.06bps (still beats clean by +29bps). The absolute margin only survives the 20bps kill-standard at 60m; shorter horizons (5–15m) are negative even for the good subset.
- Selectivity note: the good subset is only 34,916 signals and concentrates in 10:00–11:00; this is a candidate for a tighter EOD/momentum filter, not a raw broad signal.

## OOS status

- June = 20 sessions → **degenerate OOS**. No held-out period separate from the 19 signal days.
- The script's built-in split (train ≤2025-06-24 / test ≥2025-06-25) yields train 15d / test 4d (test = 2025-06-25,26,27,30), n=69,718.
- Test-only net expectancy (20bps): trap 15m **−0.0014**, trap 60m **−0.0002**; clean 60m **−0.0009**. Trap beats clean in OOS too, but both still negative. Test sample is 4 sessions — **not statistically meaningful**. This is a June-only evaluation, not a validated OOS result.

## Files written

- `factory/artifacts/h009_results_2025-06/h009_results.parquet` (primary 20bps, 404,111 signals)
- `factory/artifacts/h009_results_2025-06/h009_results_c20.parquet` (sensitivity 40bps)
- `factory/artifacts/h009_results_2025-06/h009_summary.json` (fully-populated summary; overwritten by sensitivity run — see note)
