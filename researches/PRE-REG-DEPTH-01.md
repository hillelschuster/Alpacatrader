# PRE-REG-DEPTH-01 - frozen adaptive depth-participation rule + pristine and paper evaluation plan

Registered: 2026-10-27, BEFORE any computation on the sealed holdout (2024-01..2024-12,
2025-01, 2026-06..08) and BEFORE any paper order. No rule parameter may change after this
document is committed; any change is a new pre-registration (PRE-REG-DEPTH-02).

Discovery history: selected on 2023 validation (250 sessions) by the wave-2 battery, reported
on the previously explored late block 2025-02..2026-05 (332 sessions), audited 2026-10-09
(independent bit-exact re-implementation, 122/122 fills), and robustness-checked 2026-10-27
(threshold x participation x latency x slots plateau grid, entry/exit microstructure,
signal-supply, selector upgrade, basket comparison). Evidence:
factory/artifacts/wave2_alpha_search_summary.json and the 2026-10-27 section of
researches/STATE.md.

## Frozen rule (exact semantics = factory/scripts/alpha_adaptive_depth_sizing.py, arm
## `thr300bps|p50`, as committed with this document; live path = factory/scripts/depth_bot.py
## + depth_bot_features.py, bit-parity tested against it)

1. Universe and features: the research PIT top-gainer panel, rebuilt live by
   depth_bot_features.py (exact parity on 3 frozen days: admission, rank, 26 features,
   prediction, signals - 0 mismatches). Causal-liquidity qualification as in the panel.
2. Score: immutable LightGBM h60 payoff head
   ~/alpha-data/open-search-v1/learned/models/payoff_h60.joblib (sha256 c5493c6c..., 26
   features, fit 2021-05..2022-12, never refit).
3. Signal: pred >= 0.030 on the 5-minute decision lattice; intent at t+1 (features from bars
   stamped <= t-1). No upper prediction cap.
4. Entry: marketable limit IOC at the research limit rule; arrival quote = intent + 250 ms;
   fills only if the arrival quote is fresh (<= 2 s) and supports the limit and size.
5. Size: qty = min(floor($1,000 / (limit * 1.0075)), floor(0.5 * min(ask_size, bid_size))).
6. Exit: marketable sell of the full quantity at entry + 60 minutes.
7. Book: 3 slots, $3,000 reserve, 15-minute cooldown after the actual exit, max 3 attempts
   per ticker per day, one position per ticker. Live-only safety additions (not part of the
   research edge, logged separately): daily realised-loss stop $90, hard flatten 15:55 ET.
8. Costs: primary residual rungs 25 and 50 bps round-trip on top of the touched spread
   (entry at ask, exit at bid). All-in Alpaca fees measured at ~0.2 bps (1.2 bps worst case
   from 2027-01); 0 and 100 bps reported alongside.

## Reference numbers this rule is judged against (frozen)

| block | sessions / calendar-day span | known fills / attempts | $/session @25 | $/calendar day @25 | @50 $/session |
|---|---|---|---|---|---|
| validation 2023 | 250 / 361 | 53 / 62 | +1.36 | +0.94 | +1.00 |
| late 2025-02..2026-05 | 332 / 481 | 122 / 142 | +5.95 | +4.11 | +5.30 |

Shape: median known fill -$9.21 (late), win rate 44%, top-5 fills = 125% of the late total
(fat-tail momentum payoff - the thesis, not a defect). Unknown-charged lower bound -37.47
$/calendar day late. Robustness: threshold band 0.025-0.030 positive on both blocks at every
participation (0.25-1.00) and at both 250 ms and 1 s latency (one exception: thr .025/p .25 @1 s
val -0.53); 0.020 is a validation cliff (-9 to -14 $/session).

Data cost: real-time SIP (~$99/month = ~$3.25/calendar day) is required (the free delayed feed
measured negative). Against the reference numbers this rule alone does NOT clear the data bill
on the validation basis (+0.94 - 3.25 = -2.31 $/calendar day) and only modestly on the late
basis (+0.86 $/calendar day). Capital is not the lever (bigger tickets measured worse: the edge
lives in thin books); signal supply is.

## Evaluation plan

A. Pristine holdout (one shot, requires explicit owner approval before unsealing): run the
   frozen rule once on 2024-01..2024-12 with acquired quotes for the full panel universe.
   PASS = known contribution > 0 $/calendar day at 25 bps AND > 0 at 50 bps AND >= 7/12
   months positive. FAIL = <= 0 at 25 bps. No parameter may be touched afterwards.
B. Shadow (live data, no orders): every intent journaled with the sim's would-be outcome.
   Gate to arm paper: shadow-vs-replay parity on >= 10 sessions (identical signals and
   intents given the same data) and no unexplained divergence.
C. Paper (Alpaca paper, after A or with owner override): judge after >= 30 attempts on
   (i) attempt->fill rate vs the sim's 86% (122/142), (ii) entry price vs the sim's arrival
   ask, (iii) exit price vs the sim's bid and the share of exits the sim would mark
   UNKNOWN (sim: 16 of 20 late UNKNOWNs were exits into thin/stale bids), (iv) realised net
   vs the sim's would-be net per attempt. Paper fills at NBBO and ignore displayed size, so
   paper dollars are an UPPER bound; the comparison metrics above are the evidence.
   KILL: realised per-attempt net below the sim's would-be net by more than the 25-bps rung
   on average over >= 30 attempts, or exit slippage beyond the 50-bps rung on average.

## Explicitly NOT part of this rule (tested 2026-10-27, rejected or deferred)

- Bigger tickets / leverage (measured worse; 5x on $5,000 = five worst fills -104% of equity).
- thr .025/p .75 (validation-best but one step from the .020 cliff); prediction cap at 0.05
  (validation/late disagree); wait-for-mid 5 s entry (validation up, late down, more
  UNKNOWNs); spread <= 50 bps filter (late-only); longer/shorter exits and stops (60 min is
  the grid peak on both blocks, H15/H30 fail validation); repeat/re-entry, more slots, more
  attempts (controls never bind: 0 slot-full and 0 attempt-cap rejects); compact and
  monotone selector variants (thin/empty); basket N=2/3 (all 36 cells negative and worse than
  N=1).
- Universe expansion beyond the panel's top ~11 is the open supply lever and needs new data
  acquisition - a separate pre-registration.

## Amendment 2026-10-27 (live-only ops parameter; the frozen research rule is unchanged)

The live daily realised-loss stop moves from $90 to **$300** (a catastrophe brake). Evidence
(independent review, full-coverage replay of both blocks): $90 fires on 6 validation + 22 late
days and blocks a later attempt on 3 of them - one a known winner (2025-07-15 CYCC +$120.81,
-6.1% of the late edge) - and breaks shadow==replay on those days. $300 (~1.1x the worst observed
daily loss, -$265.32) never fires in-sample, so the live bot trades exactly the frozen rule while
still capping a catastrophic day. Fat-tailed payoff: a tight stop must not cut the tail.
