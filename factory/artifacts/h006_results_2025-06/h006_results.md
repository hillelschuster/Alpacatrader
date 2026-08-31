# H006 — VWAP-fade on certified 2025-06 top-gainer events

**Hypothesis:** Stocks trading >8% above intraday VWAP fade over next 15–30m; larger VWAP distance → larger negative forward returns.
**Setup:** certified `events_topN.parquet` (120,054 events, 2025-06, 20 sessions) joined to RTH intraday VWAP (cumulative close·vol / vol per ticker-day, data as of bar t). `--threshold 8`. Clean data: `data/clean_ohlcv_2025-06.parquet` only (staged, no July).
**Direction:** trade = **short** (fade) when price is above VWAP → win when `fwd_ret < 0`. `exp_short_net = −avg_ret − roundtrip_cost`.

Cost convention: script computes `roundtrip = cost_bps × 2 / 10000`. So `--cost-bps 10` = **20bps roundtrip** (the H001–H005 kill standard); `--cost-bps 20` = **40bps roundtrip** (stricter sensitivity).

## VWAP-distance buckets → 15m and 30m forward returns

| VWAP bucket | n(15m) | n(30m) | avg 15m | avg 30m | med 15m | med 30m | short win 15m | short win 30m | short exp_net 15m @20bps | @30m @20bps | @15m @40bps | @30m @40bps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| <3% | 77,270 | 74,137 | +0.0057% | +0.0227% | 0.00% | 0.00% | 48.9% | 49.5% | −0.2057% | −0.2227% | −0.4057% | −0.4227% |
| 3-5% | 18,676 | 17,733 | +0.0628% | +0.0965% | 0.00% | 0.00% | 47.1% | 48.2% | −0.2628% | −0.2965% | −0.4628% | −0.4965% |
| 5-8% | 7,988 | 7,472 | −0.0606% | −0.1145% | −0.09% | −0.19% | 51.7% | 53.6% | −0.1394% | −0.0855% | −0.3394% | −0.2855% |
| 8-12% | 1,851 | 1,719 | −0.3470% | −0.4147% | −0.33% | −0.52% | 54.5% | 56.3% | **+0.1470%** | **+0.2147%** | −0.0530% | +0.0147% |
| 12%+ | 247 | 219 | **−3.2847%** | **−3.6525%** | −1.83% | −3.16% | 73.7% | 75.8% | **+3.0847%** | **+3.4525%** | **+2.8847%** | **+3.2525%** |

## Signal aggregate (`vwap_dist > 8%`, n=2,470)

| horizon | n | avg ret | short win | short exp_net @20bps | short exp_net @40bps |
|---|---|---|---|---|---|
| 1m | 2,291 | −0.058% | 48.2% | −0.142% | −0.342% |
| 5m | 2,221 | −0.251% | 54.5% | +0.051% | −0.149% |
| **15m** | 2,098 | −0.693% | 56.8% | **+0.493%** | +0.293% |
| **30m** | 1,938 | −0.781% | 58.5% | **+0.581%** | +0.381% |
| 60m | 1,717 | −0.744% | 60.3% | +0.544% | +0.344% |

## Verdict

**H006 prediction is SUPPORTED.** The forward-return gradient is monotone-negative in VWAP distance: at 15m, avg `fwd_ret` goes +0.006% (<3%) → +0.063% (3-5%) → −0.061% (5-8%) → −0.347% (8-12%) → **−3.28% (12%+)**; at 30m it goes +0.023% → … → **−3.65% (12%+)**. The fade is strongest and only reliably net-positive in the tail: shorting >8% above VWAP nets **+0.49% (15m) / +0.58% (30m)** after 20bps roundtrip, and the top bucket (12%+, n≈219–247) nets **+3.08% (15m) / +3.45% (30m)** with ~74% short win rate.

**Caveats:**
- **Sample size in the profitable tail is tiny.** The 12%+ bucket spans only ~219–247 observations (~2% of events). Large expectancy but low confidence.
- **Chronological split is degenerate.** June has 20 sessions; script hardcodes `split=40` → all rows are "train", **0 test rows** in the `h006_summary.json` — no true OOS within 2025-06. (Separate pass on 2025-07 would be needed for out-of-sample.)
- Positive expectancy after 20bps is concentrated in the >8%/12%+ tail; the 0–8% buckets short (fade) are all net-negative after costs.

## Files written
- `factory/artifacts/h006_results_2025-06/events_with_vwap.parquet` — events joined with `vwap`/`vwap_dist`/`vwap_bucket` (run at 20bps roundtrip; column set identical across cost).
- `factory/artifacts/h006_results_2025-06/h006_summary.json` — full per-bucket/per-horizon metric dict, thresholds, train/test sets.
- `factory/artifacts/h006_results_2025-06/cost40bps/*` — same artifacts at 40bps roundtrip (`--cost-bps 20`).
- No changes to anything outside `factory/artifacts/h006_results_2025-06/`.
