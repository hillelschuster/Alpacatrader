# PRE-REG-CENSUS-01 — Extreme top-gainer vacuum-event census

Frozen 2026-09-13. Measurement only. **No trading rule is tested or adopted.**
H025 is untouched and continues in forward paper independently. Any tradeable
formulation suggested by this census requires a NEW pre-reg + forward test.

## Purpose
Characterize the *event process* behind the H025 mechanism: a ~10% liquidity
vacuum in an attention name and its recovery. Count and describe events by
rank, prior-vacuum state, intraday sequence, halt-adjacency, time of day, and
(nomination only) attention gap. The economic target is the user's real
constraint: **expected dollars per unit of limited capital = edge x frequency x
turnover**, so the census must surface event-frequency, not just average %.
No parameter mining: the cell list below is frozen and small.

## Data (existing; no download)
- `data/leaderboard/path_YYYY-MM-DD.parquet` — causal candidate paths,
  cols [date,t,ticker,o,h,l,c,v,gain_c,n_bars]; 667 days 2024-01-02..2026-08-31.
- `data/leaderboard/lb_YYYY-MM-DD.parquet` — top-3 rows [date,t,rank,ticker,gain,px].
- Full-universe rank ladder (ranks >3) is NOT in scope here; it needs the
  full-universe bars (locally 2025-02..2026-08) and is deferred to a separate
  pre-reg. "off" rank below means: a name in the candidate list that is not in
  the top-3 at that minute.

## Event definition (identical to lb18_episodes.py / H025 semantics)
Per (date,ticker) on the ffilled minute grid, restricted to REAL new bars
(n_bars increments):
- `cm = c.cummax()`; `under = l <= 0.9*cm`; a **vacuum event** = `under` and not
  `under` on the previous row (distinct episode start).
- `prior_flush` = number of distinct vacuum starts strictly before this event
  (same session, same ticker). `seq = prior_flush + 1`.
- `pre_gap_min` = minutes since the previous real new bar (first bar of day:
  gap to the grid start, flagged separately); `halt_adjacent = pre_gap_min >= 5`.
- `depth = l_event/cm - 1`; `gain_event = gain_c` at the event minute.
- `rank_at_event` = rank from lb if the ticker is top-3 at that minute, else "off".

## Outcome metrics per event (rule-free rulers)
Using the grid strictly AFTER the event minute:
- `recovered_30m` = whether high reaches `cm` (pre-vacuum session max close)
  within 30 minutes; `t_recover_min` = minutes to that touch (else NaN).
- `max_recovery = max(high over 30m)/cm - 1` (0 if not recovered).
- `adverse_from_low = min(low over 30m)/l_event - 1` (tail measure).
No trade P&L, no B/c0 rule, no H025 parameter is recomputed.

## Frozen cells (marginals + a short frozen cross list)
1. `rank_at_event` in {1, 2, 3, off}
2. `prior_flush` in {0, 1, 2+}
3. `seq` in {1, 2, 3+}
4. `halt_adjacent` in {True, False}
5. `tod` in {AM (t<720), PM}
6. cross: rank x prior_flush; prior_flush x seq (only these two crosses)

## Reported per cell
n, events/day over the 667-day universe, `recovered_30m` rate, median
`t_recover_min`, median `max_recovery`, p05 `adverse_from_low`, median
`depth`. Chronological 3-block replication (day-thirds) of the recovered_30m
rate for every cell.

## Frozen decision rule (candidate only)
A cell is **worth a rule pre-reg** iff ALL hold:
- n >= 500 (power floor; else INCONCLUSIVE);
- events/day >= 3.0 (materially above the H025 all-fills rate ~1.9/day);
- `recovered_30m` rate within 5pp of the pooled rate AND median `max_recovery`
  within 2pp of pooled;
- recovered rate is above the pooled rate in >= 2 of 3 blocks.
A passing cell licenses a *new* pre-reg for a rule on that cell; it does not
license adoption, sizing, or any bot change. Forward paper remains the only
adoption path.

## Scope guards
- Seen-data measurement; `"measurement": true, "seen_data": true,
  "not_an_alpha_claim": true`.
- No H025 mutation; no population expansion of H025; no reopening of dead
  families (H001/H003/H005/H007/H021/H022); no ML representation here.
- Halt detection is approximate (bar-gap proxy, no LULD flags); stated as such.
- 2024-2026 carries known contamination; forward data is the only clean test.

## Artifacts
`factory/scripts/event_census.py` (+ cache `data/scratch_census/`),
`factory/artifacts/event_census.json`, `factory/artifacts/event_census.parquet`.
Ledgers appended after the run.
