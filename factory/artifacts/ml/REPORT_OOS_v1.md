# ML v1 OOS Report — VERDICT: GO (2026-09-01)

Model: LightGBM depth-6 (best_iter=57), train 2025-05+06 (243,837 events), dev 2025-07
(burned: early stop + top-decile threshold only). Single frozen pass on 2025-08..12
(293,687 events). Protocol frozen pre-peek: `OOS_PROTOCOL.md`. Costs are ROUND-TRIP,
subtracted from raw fwd_ret_60m; costs are never in training.

## All-events D10 (top-10% model score within day) @20bps RT

| OOS month | D10 net | wr | D10 t+1m entry | D1 net (avoid zone) | wr D1→D10 |
|---|---|---|---|---|---|
| 2025-08 | +10.0bps | .489 | +10.3bps | −39bps | monotone ↑ |
| 2025-09 | +49.2bps | .551 | +46.8bps | −62bps | monotone ↑ |
| 2025-10 | +23.1bps | .506 | +20.5bps | −59bps | monotone ↑ |
| 2025-11 | +19.9bps | .516 | +22.3bps | −92bps | monotone ↑ |
| 2025-12 | +6.0bps | .478 | +10.7bps | −54bps | monotone ↑ |
| **pooled** | **+22.1bps** | .508 | **+22.4bps** | −61bps | — |

n(D10 pooled)=49,200 events (~1,200 episodes). ElasticNet baseline reproduces the
D10>D1 net ordering 5/5 (magnitude ~0 — ranking structure needs trees).

## Frozen gates — 5/5 PASS → GO
1. D10 avg ≥ +3bps, ≥0 in ≥4/5 months → +21.6bps, 5/5 ✓
2. t+1m-entry avg ≥ 0 → +22.4bps, 5/5 ✓
3. wr gradient Spearman ≥ 0.8 in ≥4/5 months → ✓ (monotone rise every month)
4. no month D10 < −20bps → min +6.0bps ✓
5. ElasticNet same ordering → 5/5 ✓

## Shape of the edge
- **Ranking is the product**: wr gradient D1→D10 in all 5 OOS months; the middle
  deciles sit near the event-population mean (~−12bps @20bps); D1 is consistently
  the worst pocket (avoidance value alone, small-cap borrow makes shorts unrealistic).
- **Breadth, not selection**: episode-best (1 trade/ticker-day at top score) adds
  nothing over taking all D10 events — supports many-small-positions execution.
- **Entry timing robust**: 1-minute delay costs ~0 (fills at next bar's close).
- **Cost sensitivity**: D10 @40bps ≈ −25..−37bps → edge is sub-40bps; consistent with
  H008's volume-matched +7..+28bps. Target execution cost budget ≤30bps RT.
- Dev (July) D10 was +8.4bps — OOS months 3–15x that; no dev overfit signature.

## Accepted limitations (documented, not hidden)
- Universe tags non-PIT (2026 yfinance fetch) → mild survivorship in the top-gainer
  candidate set; identical across all months, affects all rows equally.
- Certify `n_bars≥30` full-day gate conditions on the future (survivor-flavored
  population; uniform across months).
- 60m labels end ≤16:00 → later-day events underrepresented (label-null rate ~25%).
- One regime sample per month; 2025 bull tape. Nov+Dec remain usable as a second
  re-OOS ONLY if any parameter ever changes.
- Descriptive slices below are post-peek observations — hypotheses for a future
  cycle, NOT frozen rules.

## Phase 6 next steps (sanctioned: profitable first confirmed)
1. Paper-integration recommendation for the bot (out of factory scope): score-gate
   entries to model top-decile; suppress D1-zone entries.
2. Descriptive archetype slice (tod × rvol × dist_hod) on OOS D10 — reporting only.
3. Refresh cycle when new months become available: retrain May..(n-1), score (n).

## Phase 6 — descriptive slices on OOS D10 (post-peek, hypothesis-generating ONLY)

| slice | n | net @20bps | wr |
|---|---|---|---|
| D10 all | 49,210 | +22.0bps | .508 |
| rv>3 | 39,854 | +29.7bps | .515 |
| rv<1.5 | 2,963 | −20bps | .46 |
| vwap_dist>2% | 21,485 | +50.1bps | .531 |
| vwap_dist<0 | 15,471 | −10.8bps | .490 |
| tod<240 (2:00pm) | 45,210 | +28.3bps | .514 |
| tod 240–330 | 14,059 | +3.6bps | .488 |
| dist_hod / rank | — | ~flat within D10 | — |

Model sanity-check: it independently rediscovered H008's RVOL>1.5 structure (rv>3 pocket,
negative rv<1.5) with no hand-coding. dist_hod and rank add little within D10.

### Composite cell (SELECTION-BIASED — needs fresh-month validation)
- `D10 & rv>3 & vwap+2% & tod<240`: n=13,180, **net +67.8bps, wr .534**, monthly
  +29/+97/+57/+89/+60bps — positive 5/5.
- Same 3 filters WITHOUT model on full population: +9.6bps only → score adds ~58bps
  on top of rules (7x). Model is not redundant with hand rules.
- Bias note: rv>3 = prior knowledge (H008); vwap_dist>2% and tod<240 were selected
  by looking at these OOS months. The +67.8bps is an upper bound; true value must be
  set by a pre-registered composite on future months (or Nov+Dec-only re-OOS with
  filters built from Aug–Oct only). Not a claim.

## Phase 6 final — nested composite VALIDATED on untouched months (2026-09-01)

Method: threshold grid (rvol×vwap_dist×tod, 27 combos) evaluated on Aug–Oct ONLY;
best cell frozen and applied to Nov–Dec, which never entered any selection.
- Selected (Aug–Oct): rv>4.0 & vwap_dist>3% & tod<270 → +78.4bps there (n=6,125)
- **Frozen Nov–Dec: +89.4bps @20bps, wr .605 (n=3,847; Nov +101, Dec +73)**
- Ref D10-all Nov–Dec: +12.6bps → composite adds +77bps on held-out months.

Label-complete rows (n=3,444): @20bps **+102.6bps** wr .620 | @40bps **+82.6bps** wr .589
(SURVIVES 40bps — unlike the bare D10 pocket) | t+1m entry +101.1bps.
38 sessions: 63% positive days, daily event-mean +26.2bps, maxDD 7.2% (daily-mean units),
events/day p50=98 p90=196 (capacity: needs per-name caps; ~58 episodes/day universe).

Status: strongest validated result of the project. Composite rule = candidate v2
strategy. Caveats: 2025 tape only; non-PIT universe tags; certify n_bars≥30 gate;
2 validation months at composite level (n=3,444). Next cycle: retrain on May..Oct,
re-freeze, score 2026 months when data lands; sizing research before any live use.
