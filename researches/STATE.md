# STATE — current truth (snapshot 2026-09-13). Full chronicle: factory/STATE.md (append-only log).
# Hypothesis ranking + falsifiers: researches/HYPOTHESES.md (living).
# History before 2026-09-04: researches/CANONICAL_STATE.md (superseded snapshot, kept for trust map).

## Where we are (2026-09-13)
Phase: LIVE PAPER VALIDATION of the first OOS-passed mechanism; research lanes closed;
waiting on forward fills.
- **H025 FLUSH RULE = OOS-PASS-PAPER.** Frozen `PRE-REG-FLUSH-01.md` passed untouched OOS
  2024-01..2025-02 (pf>=2 n=381, +1.14%/trade net 100bps, 12/14 months). `flush_bot.py`
  v2.1 is armed `live:true` on a dedicated Alpaca paper account (flat, 0 fills as of
  2026-09-13). Mechanism: extreme top-3 leader in fresh+thrust state gets a resting -10%
  bid; fast flush fills at B; exit toward c0 (OCO target at c0, 0.9B stop, tl30).
- **Judge live fills against the A3b baseline, NOT the frozen one** (HANDOFF §14):
  pf2 +1.13%, 10/14 months, ~19 fills/mo post-overlay, worst -3.1%; fragility: 55% win,
  6.3pp cushion, bootstrap CI touches 0, ~14 months of forward fills needed to exclude zero.
- **Closed (tested negatives):** post-fill management incl. flat/conditional time-stops
  (`PRE-REG-EXIT-01`, H028 RETIRED) - the edge IS the resting limit at c0; IEX-only feed
  replay (`PRE-REG-MICRO-01` Study A) - failure was a fill-venue artifact, the state feed
  survived via A3b; unsupervised pattern digests (all-minutes v1/v2 and event-anchored
  E1/E3; `PRE-REG-PATTERN-01/02`, H030 RETIRED) - no separable shape families at
  1-min/60-min on the top-3 population.
- **H029 day-breadth (user hypothesis) DOWNGRADED** (H029r): quiet days (0-1 strict names)
  beat busy days OOS (+1.20% vs +0.46% all fills; rank1 -0.69% vs +1.33%) but the
  day-clustered bootstrap CI includes zero and permutation p=0.19 - OOS-only, not robust,
  NO gating. Live `n_strict_est` + `pf_est` tags flow into the ledger for a free forward test.
- **Next:** forward paper accumulation (~30 fills) -> judge vs A3b -> tiny real-money
  sizing decision. Remaining ML branch = learned sequence embedding (own pre-reg) or more
  data/power (2021+ backbone staging, real-time SIP); both are priced decisions. SIP
  ~$99/mo is NOT needed for the frozen strategy (IEX covers a median 24.6% of session
  minutes per top-3 name; only sub-minute features would justify it).
- The older sections below (phenomenology, collisions, 2026-09-08 strategy) remain valid
  history; the 2026-09-08 "current strategy" block is superseded by the flush-rule path.

## What is solidly established (multiple provenances, harness-verified)
- Phenomenon: ~1.2-1.5 runner days (+60%+ open->close, $1-50) per day, all 9 months
  scanned; zero dead months. Frequency is regime-stable.
- Runner shape: median open->high 322min; half the move completes by ~11:25 only 15%
  of the time; afternoon contributes median 55% of the move. Halts fingerprint the
  process (84% of runners halt; dose-response 0 halt -> +83% mean gain, 11+ -> +177%).
- Thrust alone is DEAD (+0.3% remainder, n=5,521). Rank/health/E1/E2/E3/Ridge/DTW/
  snapshot selection: all failed conditionality at scale. Retired, thesis-level.
- Buying halt reopens is DEAD causally (next-bar-open D30 -0.5% to -1.5%). The
  halt-gap money (morning qualifying events: mean +6.3%, median +4.4%, n=68) accrues
  only to the already-long.
- MFE-before-drawdown is the most stable measured property of top-gainer cohorts
  (mb100 positive in 9/9 month blocks, 31/33 days) — an opportunity map, NOT an
  entry signal (H11 was its capture attempt and failed).
- Money-location map (measured on the GATE cohort — 15-30% by 10:30 + >=1 halt,
  n=180 dev name-days; not general-population): morning halt-gaps (if holding),
  10:30-11:00 inflow wave (+1.2%), afternoon 14:00-16:00 wave (+2.0%). Midday is
  dead capital. The +2.39% afternoon rental did NOT survive collision months
  (2025-04 -6.27%, 2026-01 -3.51% net; pooled -2.13% at 100bps).

## Collisions: 0-for-4 (the meta-finding that shapes everything)
E1xhealth (146d) -> failed. Ridge (one formulation) -> failed twice (Aug, Oct). DTW
medoid October -> failed. H11 afternoon rental -> failed (2026-09-08). Pattern:
month-blocked dev positives with small per-month n are found easily and do not
replicate. SCOPE: this covers small-n selection/timing rules; the ML fresh-window
failure had high n (5/5 months negative) — that implicates regime/decay. Power is
necessary, not sufficient. Consequence: no selection or timing rule gets believed
again without (a) pooled n across MANY months before testing, or (b)
forward-observer accumulation as primary evidence.
Full retire list with causes: H8 gate (descriptive only; capture failed via H11);
H9 reopen participation (anchor illusion; causal entry negative); H10a hold-through-
halt (UNTESTED — open family, not promising); H10b/H11 afternoon rental (collision-
failed); plus historical: E1/E2/E3, gain-rank, health, Ridge, DTW, 10:00 snapshots.

## Current strategy for the next phase (decision 2026-09-08)
1. STOP inventing single-month-sliced selection rules. Statistical power first:
   any new test must pool >= 6 months of name-days in dev AND pre-register >= 2
   unseen collision months.
2. Forward observer = the honest evidence engine (P2 pre-reg frozen: researches/PRE-REG-P2.md — read before any measurement work). It runs daily, order-free, and
   accumulates live n (no backtest overfit). Priority: keep it running, score every
   session through score_forward_day.py, build the live record.
   RUNBOOK: `python factory/scripts/forward_observe.py --live` during ET market hours
   (logs to data/forward/YYYY-MM-DD/; starts idling until session open). After close:
   `python factory/scripts/score_forward_day.py <YYYY-MM-DD>` writes scores.json.
   Skip weekends/holidays (09-05/06 rows are weekend noise). It is NOT currently
   scheduled — a session must launch it manually each trading day.
3. Phenomenology remains the idea mine (money-location map above), but extraction
   formulations must be power-aware from inception.
4. Open (untested, not promising): hold-through-halt capture; turnover/float clock
  (needs PIT float data we lack); failed-move fade (conditions on late info);
  transitions/handoffs (needs event logging wiring).

## Open questions (unresolved; do not cite either side as settled)
- Rank-1 chasing conflict: Cameron-lane "causal minute-rank-1 negative every month
  both years" vs verify_core 11-point monthly h60 DOWNGRADED to mixed (-579..+411).
  Different horizons/universes; never re-opened head-to-head.
- FP-penalized recall gate for Stage B (MFE-before-DD target adopted).
- Harness-path rerun of MFE magnitudes (lane-only provenance currently).
- RESOLVED 2026-09-08: clean 2026-04..08 moved into data/backfill/ (live read path); load_day verified OK on 2026-04 and 2026-08; stale data/clean_2026-03 duplicate deleted.
- H10a hold-through-halt: the one open capture family (untested, power-caveated).

## Critical session-semantics (never re-learn these)
- et_minute() in replay_watchlist.py; session ET [570, 960); snapshot 10:00 uses
  et<=599, targets et>=600. PM data only via return_pm=True (separate frame).
- Month->path: >=2026-03 loads data/backfill/, else data/. 2026-04 clean file absent
  locally. 2026 premarket coverage absent; repaired premarket = 2025 only.
- MFE/MAE conventions: outcome_MB() low-based, entry-bar-included, same-bar
  drawdown-first (conservative). Never mix with older looser MFE numbers.
- Halt-gap proxy = >=5min hole in RTH minute bars. Trustworthy only for fast names
  + long holes; flat names print ~1M spurious holes (measured, excluded).
- Friction: use 100bps round-trip minimum for these names; adversary case says
  50-150bps realistic. 20bps assumptions are void.

## Live evidence sources
- factory/artifacts/*.json — every number above has a committed artifact.
- data/forward/2026-09-04..06/ — live observer days (09-05/06 are WEEKENDS —
  exclude non-trading days in analysis; 09-04 has the 16% phantom/warrant issue).
- H11 collision artifact: factory/artifacts/h11_collision_results.json (n=112).

## Provenance doctrine (adopted 2026-09-08 after audit)
- Every artifact's producer script must be committed under factory/scripts/.
- Every cohort scan must report distinct days covered (the 281-vs-245 gate-cohort
  confusion was a day-coverage gap, not a population difference).
- Friction is annotated per claim; cross-friction comparisons are void (20bps ML-era
  numbers are not comparable to 100bps H-era numbers).

## File-map doctrine (from 2026-09-08 mapping round — complete order)
- researches/: INTENT/STATE/HYPOTHESES active; CANONICAL_STATE + 07 frozen history;
  history/ = June bot-era audits (00-06), zero research constraint.
- factory/scripts/: ACTIVE = replay_watchlist, forward_observe, score_forward_day,
  test_forward_observe + data-ops chain (download/audit/clean/certify/validate/
  run_fresh_window) + ML machinery (build_features, train_ml, live_admission,
  sequencing, exposure_design, eval_frozen); RETIRED-KEEP = all evidence producers
  (H-series, path program, runner/gate/halt/h11, stagea, verify_core, feed_fingerprint);
  DEPRECATED = rank_day, extract_events (lineage only).
- factory/artifacts/: 8 committed JSONs = EVIDENCE-KEEP; ml/ specs+models = LINEAGE;
  h006-010 summary/report = EVIDENCE, parquets regenerable (c10/c20 dup pairs pruned).
- data/: canonical = clean_ohlcv (2025 data/, 2026 backfill/), premarket_2025,
  forward/ = live evidence; raw ohlcv = rebuild source; _scratch/_superseded =
  scratch. CLEAN 2026-04..08 NOW IN BACKFILL (was orphaned; P2 unblocked).
- Bot docs: SOUL/SPEC current (header version stale); README stale (pre-runner era);
  bot = separate workstream, implements only validated research.

## P2 verdict (2026-09-08, pooled 13mo)
Map structure replicates at ~half magnitude; M4 level (+0.59% gross) is
all-tail (excl top-5 -> -0.09%) and cannot survive friction. H11 death confirmed
structural. P3 restricted to replicated components; own pre-reg required.

## Leaderboard contract (2026-09-09, binding — do not drift)
- TradingView-equivalent: US listed stocks (NASDAQ/NYSE/AMEX), stock/common,
  junk-ticker suffixes excluded; rank causally by gain vs IMMEDIATELY previous
  trading session close, desc.
- Causal price at decision time t = close of the last bar stamped et<=t-1
  (bar t-1 closes at t; no shift(1), no extra staleness).
- ARBITRARY decision times supplied by the research. The leaderboard function
  must NOT bake in any H12 grid, target, horizon, or evaluation structure.
- Live source: TV scanner API (factory/scripts/tv_leaderboard.py, cmd live).
  Historical: same script, cmd hist — prev-close crosses month files by
  IMMEDIATELY prior session date, not prior month's last session.
