# PRE-REG-FLUSH-01 — frozen flush-bid rule + true OOS evaluation

Registered: 2026-09-09, BEFORE any out-of-sample computation.
Mechanism discovered in-sample on 2025-03..2026-08 (18 months, all previously seen).
This document freezes the exact rule and the OOS plan. No parameter may be
changed after this document is committed.

## Frozen rule (exact semantics = lb18_roll.py engine @ commit 3508976)

1. Leaderboard: causal top-3 by gain vs immediately previous session close,
   PIT universe (data/pit/pit_symbols.parquet), 1-min bars, new-bar-only touches.
2. State minute: ticker in top-3 AND gain >= 1.00 AND pullback >= -0.01
   (c / session cummax(c) - 1) AND r15 >= 0.03 (c / c[-15] - 1).
3. Rolling bid: while flat and a new bar arrives, refresh a resting bid
   B = 0.9 x close(latest state minute strictly before the current bar).
   Bid expires 120 minutes after the last refresh. One order per ticker at a time.
4. Fill: first NEW bar (n_bars increment) with low <= B; fill assumed at B
   (no price improvement, no queue modeling).
5. Exit (tl30): from the fill bar onward, per new bar:
   (a) low <= 0.9 x B -> exit at min(open, 0.9 x B) [stop checked first];
   (b) high >= c0 -> exit at c0 (target = anchor-state close);
   (c) else after 30 bars -> exit at that bar's close.
6. Friction: 100bps per trade (0.01) subtracted from every trade.
7. Re-arm: after exit, flat until the first new bar with t > exit minute; the
   next strictly-later state minute may then place a new bid.
8. Primary subset: fills whose anchor state minute had prior_flush >= 2
   (causal count of prior flush-episode starts: low <= 0.9 x cummax close on
   new bars earlier that session). Secondary: all fills.

## OOS block

- Months: 2024-01..2025-02 (14 months), untouched in every prior analysis.
  2023-12 is downloaded only as prev-close bridge for 2024-01-02; not evaluated.
- Data path identical to dev: mito0o852/OHLCV-1m download, clean_month.py
  defaults (RTH 9:30-16:00 ET, dedup keep-first, close >= $2.00, vol >= 100),
  lb18.py leaderboard build, frozen sim engine.
- Evaluation script: factory/scripts/lb18_oos.py (faithful port of the
  lb18_roll.py engine, month-filtered). Before OOS months are touched, it must
  reproduce the dev-span stats from lb18_roll.json exactly
  (all +0.39%/11of18; pf2 +1.20%/15of18; rank1 +0.96%/13of18).

## Gates

- PRIMARY PASS: pf>=2 pooled mean net >= +0.30%/trade AND >= 10/14 months positive.
- FAIL: pf>=2 pooled mean net <= 0.
- Otherwise: INCONCLUSIVE (report; no claims).
- Secondary (reported, non-gating): all-fills stats, rank1, median, %positive,
  worst month, worst single trade, n. Tail metrics are position-sizing inputs,
  not mechanism gates.
- No re-tuning, no new conditioning, no parameter change on OOS months.
  Any forced deviation (e.g. missing data month) is documented in the artifact.

## Rationale

Every element of the rule was selected while observing the 18 dev months;
selection risk is the dominant uncertainty. 14 adjacent unseen months provide
~500 pf2 fills at the in-sample rate (~36/month). Underlying discovery record:
researches/HYPOTHESES.md sections 2026-09-09n / 2026-09-09o.
