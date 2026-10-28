# Signal-supply widening study

## Verdict

The quote-backed depth lane is not slot-, attempt-cap-, or meaningfully cooldown-limited. The three-slot baseline has **zero slot-full rejects** in both blocks; relaxing attempts changes nothing; adding slots from 3 to 4/5/6/8 changes no trades or dollars. The broad repeat/no-cooldown variants add only a few fills, reduce dollars/fill and median fill, and deteriorate validation at 25–100 bps. Keep the existing 15-minute cooldown / 3-attempt / 3-slot baseline; no tested widening adds validation dollars.

The one weak late-only exception is a 5-minute cooldown + high attempt cap: +$0.08/calendar-day late at 25 bps versus baseline, with no validation gain, +1 late known fill, and slightly lower median fill. That is not a durable plateau. Validation selects baseline (ties resolved by first-listed baseline); late results are descriptive only and the late block was already explored.

## Method

Used the frozen adaptive depth `thr300bps|p50` states, h60 prediction threshold 0.030, same real quote execution resolver, intent-ask IOC limit and 250 ms arrival, causal displayed-depth participation cap, and 60-minute bid exit/depth check. Residual cost scenarios are 0/25/50/100 bps round trip on top of ask/bid touch prices (spread is already included). Known contribution excludes UNKNOWN executions; UNKNOWN counts are reported separately. Per-ticket budget stays $1,000; higher slot variants scale nominal book to $4k/$5k/$6k/$8k. No orders were sent.

Validation is 250 sessions / 361 calendar days; late confirmation is 332 sessions / 481 calendar days. Selection is maximum validation known dollars/calendar-day at 25 bps. Tables show every variant; each cost vector is **calendar-day dollars / session dollars** in order 0, 25, 50, 100 bps. `$ / known fill` is average; median is separately shown. Top-5 share is the net contribution of the five largest individual known fills divided by total known contribution.

## Funnel: baseline

| Block | Qualified state rows | Score signals | Passed intent/quote/spread gate | Funded | Non-no-match outcomes | Known | UNKNOWN | No-match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation | 149,284 | 107 | 96 | 62 | 61 | 53 | 8 | 1 |
| Late confirmation | 247,345 | 244 | 225 | 142 | 142 | 122 | 20 | 0 |

| Reject reason | Validation | Late confirmation |
|---|---:|---:|
| Intent not firm/fresh | 11 | 17 |
| Spread above prediction bar | 0 | 2 |
| Quote not acquired / min capital / zero depth / unsupported depth | 0 | 0 |
| Active-ticker overlap | 31 | 74 |
| Cooldown | 3 | 9 |
| Attempt cap | 0 | 0 |
| Slots full | **0** | **0** |
| Cash shortfall | 0 | 0 |

The funnel makes the binding point clear: many score events are duplicate ticker states while a position is active; the larger lost subset is overlap, not portfolio slots. About 19% of funded events are UNKNOWN on validation (8/62) and 14% late (20/142); quotes support the modeled touch/depth, not guaranteed exchange fills.

## All variant results

Each `cost/day (cal/session)` cell gives the four rungs as `0 / 25 / 50 / 100 bps`. Fill-quality columns are known fills, average $/known fill at 25 bps, median $/fill at 25 bps, top-five share at 25 bps, and positive known-contribution months / months observed. Calendar/session rates use the actual fully replayed blocks.

| Variant (slots; cooldown; attempt cap) | Validation cost/day (cal/session) | Val known / unknown; avg / median fill; top5; positive months | Late cost/day (cal/session) | Late known / unknown; avg / median fill; top5; positive months |
|---|---|---|---|---|
| Baseline (3; 15m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Cooldown 10m (3; 10m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.50/+6.52; 25: +4.05/+5.86; 50: +3.60/+5.21; 100: +2.69/+3.90 | 123/20; +$15.82 / -$9.29; 126%; 10/16 |
| Repeat 5m (3; 5m; 20) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.63/+6.71; 25: +4.18/+6.06; 50: +3.73/+5.41; 100: +2.83/+4.11 | 123/20; +$16.36 / -$9.29; 122%; 10/16 |
| Repeat, no cooldown (3; 0m; 20) | 0: +1.05/+1.52; 25: +0.78/+1.12; 50: +0.50/+0.73; 100: -$0.04/-$0.06 | 56/8; +$5.01 / -$6.92; 565%; 4/12 | 0: +4.05/+5.87; 25: +3.59/+5.21; 50: +3.13/+4.54; 100: +2.21/+3.20 | 126/21; +$13.72 / -$10.70; 142%; 9/16 |
| Attempts cap 4 (3; 15m; 4) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Attempts cap 5 (3; 15m; 5) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Attempts cap 10 (3; 15m; 10) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Slots 4 (4; 15m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Slots 5 (5; 15m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Slots 6 (6; 15m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Slots 8 (8; 15m; 3) | 0: +1.20/+1.73; 25: +0.94/+1.36; 50: +0.69/+1.00; 100: +0.18/+0.27 | 53/8; +$6.43 / -$5.49; 465%; 4/12 | 0: +4.56/+6.60; 25: +4.11/+5.95; 50: +3.66/+5.30; 100: +2.75/+3.99 | 122/20; +$16.19 / -$9.21; 125%; 10/16 |
| Open supply (8; 0m; 20) | 0: +1.05/+1.52; 25: +0.78/+1.12; 50: +0.50/+0.73; 100: -$0.04/-$0.06 | 56/8; +$5.01 / -$6.92; 565%; 4/12 | 0: +4.05/+5.87; 25: +3.59/+5.21; 50: +3.13/+4.54; 100: +2.21/+3.20 | 126/21; +$13.72 / -$10.70; 142%; 9/16 |

The cost ladder is net known dollars after actual touch pricing and the listed residual. At 25 bps, the maximum validation row contributes $340.88 total over 361 calendar days (+$0.94/day, +$1.36/session). Late baseline contributes $1,975.11 total over 481 days (+$4.11/day, +$5.95/session). Validation remains highly concentrated: its top-five share is 465%, so a few winners offset larger aggregate losses; 4 of 12 months are positive. Late top-five share is 125% and median fill is negative.

## Interpretation and limits

- Relaxing attempts has exactly zero effect: baseline attempt-cap rejects were zero in both blocks.
- 4/5/6/8 slots produce the identical replay because slots never bind; the book scales with slots, so this does not imply returns at a fixed $3,000 book.
- Re-entry at 5 minutes adds only one late known fill and improves late $/day by about $0.08 (1.9%) while validation is unchanged. Zero cooldown gains 3 late fills but cuts late known $/fill from $16.19 to $13.72, worsens median by $1.48, loses about $0.51/day at 25 bps late, and lowers validation from $0.94 to $0.78/day. The similar fills under attempt caps 3–10 and slot caps 3–8 establish a broad null plateau, not a profitable capacity spike.
- The precomputed panel is a narrow watchlist, not a top-20 universe: validation panel files contain `rank_snapshot` only through rank 11 (ties), and depth state rows omit rank admission metadata. A top-20 admission comparison cannot be formed from this data; no price-floor widening was claimed/tested. Broader universe supply needs new candidate acquisition/scoring states.
- UNKNOWN executions are excluded from the known-dollar numerator rather than assumed filled or assigned favorable outcomes. Hence these are partial known contributions, not a live P&L estimate. The modeled quote-supported ask/bid touches do not promise exchange fills.

## Recommended actions

1. Keep the current baseline admission controls; do not increase slot capital or remove the cooldown based on these results.
2. Do not promote the 5-minute repeat cell: it is late-only marginal and validation-flat. Seek supply by genuinely expanding the candidate universe, which requires out-of-watchlist panel/acquisition data.
3. Preserve a paper-only state; this research run placed no orders and is not a live deployment authorization.

## Artifacts

- Producer: `factory/scripts/alpha_signal_supply.py`
- Results and funnel: `~/alpha-data/open-search-v1/signal_supply/results.json`
- Per-variant, per-day resumable parts: `~/alpha-data/open-search-v1/signal_supply/parts/<variant>/<block>/`
