# PRE-REG — BASKET-01 Phase 1: population & path anatomy (measurement contract)

Status: FROZEN upon first run. Descriptive only — no P&L claims, no economic kills, and
**no release-rule or survivor-rule constants** (those are deferred to PRE-REG-BASKET-02,
written after Phase 1 completes and before any Phase-2 P&L is viewed). Lane: BASKET-01.
Created 2026-09-16. Companion: `researches/THESIS-BASKET-01.md`.

Sequence (do not reverse): **measure the race -> understand survival/death geometry ->
formulate one simple faithful implementation -> freeze it -> test economics.**

---

## 1. Data & machinery (audit before any table)

Sources: `data/clean_ohlcv_YYYY-MM.parquet` (raw RTH bars; months >= 2026-03 in
`data/backfill/`); `data/backfill/premarket_ohlcv_2025-*.parquet` (2025 only); PIT universe
`data/pit/pit_symbols.parquet`; `data/split_flags.parquet`.

Conventions: ET session [570,960); decision on the last COMPLETED bar (et <= T-1); fills at
the next bar open; never use `data/leaderboard/path_*.parquet` as an anatomy price source
(its row t holds bar t-1, ffilled).

Audit checklist (run first; report results with the tables):
missing 09:30 bars per month; duplicate (timestamp,ticker) rate; symbol-reuse checks;
premarket print staleness (2025); candidate-day bar coverage; half-day sessions; and the
split-file audit — characterize `data/split_flags.parquet` (provenance window; signature
= overnight ratio >= 2.5 with |intraday| < 0.30, so genuine running news gaps pass
through) and list every candidate name-day with anchor-ratio >= 2.0 plus its
classification (genuine news gap vs reverse-split artifact). **Causal-eligibility audit**:
every field used to build the candidate set at T must be knowable at T — full-session bar
counts, later halts, later fillability, and EOD outcomes are outcome-only fields and are
audited to never enter eligibility. Execution-realism sampling: pull SIP trades/quotes for
a small predeclared sample of candidate name-days (lb18_fills_micro pattern) to bound
spread/slippage where feasible.

## 2. Universe — broadest defensible; strata reported

PIT listed common stock (NYSE/NASDAQ/AMEX); same-day availability via the latest vintage
<= day (`lb18.elig_for` pattern); price >= $1 at decision time; the ranking bar(s) exist
(A_open: the 09:30 bar; A_pm: a premarket print meeting the freshness rule; B(T): at least
one completed bar with et <= T-1). **No eligibility field may depend on how the rest of
the day unfolds**: session bar count, halt count, fillability-after-T, and EOD outcomes
are outcome/strata fields only. Do NOT inherit another strategy's screen ($2/$5M certify
filters, runner $1-50 band) as a pre-selection.
Corporate actions: `data/split_flags.parquet` entries are **audit/sensitivity metadata
only — never causal admission criteria**, because the flag's own signature uses the
stock's full-day intraday behavior, which is future information at decision time. Flagged
candidates stay in the basket; every table is additionally reportable with flagged names
removed as a sensitivity cut. No independent corporate-action source exists in the data,
so there is no causal split filter; candidates with anchor-ratio >= 2.0 remain listed in
the audit for classification.
Report every table stratified by price band, dollar volume, decision-time bar count, halt
count, and warrant/penny flags where inferable. The executable Phase-2 universe is frozen
later for operational reasons only — never because a band produced attractive P&L.

## 3. Populations (A and B coequal; overlap retained)

- `A_open`: rank from the completed 09:30 bar by open(09:30)/prev_close - 1 (information
  learned from the opening RTH print); fill at the 09:31 bar open (1-bar lag).
- `A_pm`: rank by last premarket print <= 09:30 / prev_close - 1 (known before the bell);
  fill at the 09:30 bar open — the first realistic RTH participation opportunity (09:30
  open = first RTH trade; no official auction print exists in this data). Conservative
  bound: 09:31 open, reported alongside (regenerated as a coequal `A_pm31` population:
  same selection, fill at the first bar open with et >= 571). Freshness rule default:
  print within the last 15 minutes before 09:30; staleness sensitivity reported.
  [AMENDED 2026-09-20: available for all 1,066 dev days under SIP — the legacy 2025-only
  limit was a premarket-data availability artifact.] Never pooled with A_open.
- `B(T)`: rank by close(last completed bar, et <= T-1) / open(09:30) - 1; fill at the
  first bar open with et >= T. Frozen timing surface (fixed before any aggregate result
  was viewed): 5-minute resolution through the opening window — 09:35, 09:40, 09:45,
  09:50, 09:55, 10:00; 15-minute resolution to the end of the first hour — 10:15, 10:30,
  10:45, 11:00; 30-minute resolution to noon — 11:30, 12:00. Rationale: the first ~30
  minutes mix opening repricing, stale premarket leadership, violent rank churn and
  wide spreads/halts with the largest remaining upside, so the open window gets the
  finest ruler; the coarser later points measure the "identity resolved but tail
  already consumed" arm. The grid is an anatomical surface for the early-noise vs
  resolved-identity trade-off, not a P&L search grid; per the DOF rules, T choice
  consumes development freedom and cannot be selected from these aggregates.
- Per-T joint reporting (aggregation layer): for each (population, T, N) the tables are
  read together — eventual-leader containment; remaining post-fill executable upside of
  that leader; rank churn / new entrants / leadership stability; adverse path and
  failed-ticket burden of the rest of the basket; execution realism (touch vs exec,
  blocked slots, gaps). No single metric is interpreted alone.
- Anchors are per-population by design: A = previous-session close; B = RTH open. The other
  anchor is recorded descriptively per candidate (cross-population comparability); neither
  is forced onto the other population.
- Selection-time gain from each population's anchor is recorded per candidate and used as a
  descriptive stratification axis (low vs high gain already achieved at T) — not a filter.
- N ladder: 1/3/5/10 descriptive. Fewer than N qualifiers = fewer slots; unfilled slots are
  cash; empty days remain in denominators. A∩B overlap is measured and reported, not
  excluded.
- Rank turnover: full churn matrices P(i in topN(T') | i in topN(T)) and new-entrant shares.

## 4. Rulers (descriptive only — never targets or definitions)

- Upside ladder from fill: +5/+10/+20/+30/+50/+100%. Adverse ladder: -3/-5/-8/-10/-15%.
- Both touch-based (minute extreme) and conservative executable (next-bar-open after
  threshold) forms where applicable. A minute-high touch is an upper bound, never a capture.
  If the touch occurs on the final available bar, the executable form is N/A (non-executable
  touch); never substitute that bar's close.
- Same-bar both-touch: flag AMBIGUOUS; report optimistic and pessimistic bounds; use
  sub-minute evidence where available (`data/subminute/*_exit_quotes`; sampled SIP) — never
  silently decide. Adverse-first is allowed only as a labeled stress bound for execution
  simulation, not as observed truth. Every ordering table reports its AMBIGUOUS share
  explicitly, so the size of the unknown stays visible.
- Friction presentation for any economics-flavored table: gross; realistic executable
  estimate (sampled); 100bps stress; 150bps stress. Stress testing is required; deliberate
  pessimism is not measurement accuracy.

## 5. Tables (all month-blocked; full reporting; no T/N mining)

**Discovery posture (how these tables are read — frozen with the contract).** This pass is
a mechanism- and implementation-discovery instrument, not a rejection gauntlet: full
reporting is mandatory, unfavorable cells included, and the anatomy actively looks for the
thesis's practical signatures — through the frozen tables only:

1. accessible post-entry right tail -> T7b executable lenses (touch vs exec vs
   exec-at-or-above), T5 MFE ranks, T3/T4;
2. potentially multiple valuable survivors -> T7b k>=2 and all-3 day frequencies (touch and
   exec lenses);
3. meaningful remaining opportunity after identification improves -> T2 (share of the move
   ahead of our executable fill; remaining %), read jointly with T1 churn across the frozen
   timing surface;
4. asymmetric failure/survival geometry -> T3/T4 retention vs co-member loss, with
   AMBIGUOUS bars reported as bounds;
5. a region where failed participation is inexpensive without prematurely eliminating
   exceptional movers -> the T4/T7b knee together with the T6 execution buckets.

The search is for where/when/under what conditions the mechanism appears strongly enough
for a simple causal implementation; these tables are the pre-declared way to look.
Implementation artifacts are never treated as thesis evidence; T/N/T-selection mining
remains prohibited (the frozen grid is reported in full).

**Evidence boundary (fixed before viewing anything):** Phase-1 tables and every BASKET-01
computation run on the development span = all available months EXCEPT the reserved unseen
months **2026-06, 2026-07, 2026-08**. These three months are excluded from every Phase-1
table and from Phase-2 rule formulation; they are inspected one-shot under the frozen
PRE-REG-BASKET-02 rule. (They have been used by other, closed formulations and may be
checked for basic data presence, but they are unseen for BASKET-01 specifically.) Long-run
validation beyond that is forward. Development span: 2021-01..2023-12 + 2025-02..2026-02
(clean) + 2026-03..2026-05 (backfill); note the PIT universe begins with the 2021-01-30
vintage, so the first usable day is 2021-02-01 (2021-01 days have no causal universe and
are skipped). [AMENDED 2026-09-20 — availability amendment, not a semantics change: the
original text read "A_pm additionally limited to 2025-02..2025-12 (premarket files exist
for 2025-01 but there are no RTH clean bars for it)". Under the SIP substrate, premarket
tables exist for the full span, so A_pm is now computed for the same 1,066 dev days as
every other population. Same idea, earlier observation point; the population definition
(rank by last premarket print <= 09:30 with the freshness rule, scored vs prev close) is
unchanged.]

**T1 — Composition & turnover**: candidate sets per (population, T, N); churn matrices;
new entrants; days with <N and 0 candidates.

**T2 — Containment (IDENTIFICATION; diagnostic only)**: at each T, share of the day's
session-max top-1/top-2/top-3 already inside our top-N; share of their eventual session-max
move still AHEAD of our executable fill; share of the move already completed before T.
Terminal/EOD-leader variants included. This answers directly: did we hold tickets for the
actual race winner, or was the winner not yet in our bucket? Missing the champion does NOT
falsify the thesis (see THESIS §2).

**T3 — Competing-risk first passage**: P(+H first) / P(-L first) / P(neither by EOD) over
the full ladder matrix, per (population, T, N); ambiguous bars flagged.

**T4 — Release-retention frontier** (ruler L — not a Phase-2 rule): F(L,H) = P(at least one
filled member reaches executable +H before its own -L); Q_H(L) = P(still held at first +H |
+H occurs); co-member loss distributions; P(all slots -L first); gap-through bucket reported
separately from clean fills (never assume fills at the barrier; use min(open, level)).

**T5 — Runner path anatomy (retrospective)**: MFE/MAE, time-to-high, retracement-before-high,
halt counts, EOD-close vs high, per excursion strata; overnight vs intraday decomposition of
the monster move (to price the forced-flat constraint).

**T6 — Structural realism buckets**: clean vs gap-through fills; halted-at-entry = the
candidate stays in the intended set but becomes a blocked/unfilled slot (reported, never
a silent exclusion); halted post-entry = reopen execution (flagged uncertain; no LULD
flags in data); unfilled slots; involuntary overnight carries (halted through EOD).

**T7 — Basket-level descriptive (policy-free)**: ex-post pay-for-team under explicit
stylized scenarios — (a) all-hold-to-EOD realized member returns, (b) ruler-based
failed-ticket costs (-3/-5/-8/-10) — P(best member pays for all others), mean surplus/
deficit; both labeled hypothetical. Break-even map: policy-free illustration here
(all-hold / ruler scenarios); the executable policy version belongs after PRE-REG-BASKET-02.
Fixed overnight shadow ledger (EOD -> next executable open; diagnostic only — never used to
repair the intraday result). Policy pay-for-team (what the rules actually kept exposed) is
deferred.

**T7b — Joint future right tail of the original basket (multi-survivor preservation)**: the
hypothesis assumes **no fixed number of survivors** — a day may produce 0, 1, 2 or all 3
original top-3 members with large post-fill excursions, and multiple members may warrant
participation simultaneously. Per (population, T, N=3), per ladder H, month-blocked:

- distribution of k = 0/1/2/3 original top-3 members producing the +H future excursion,
  for the **touch lens** and the **executable lens** separately. The executable lens counts
  only members with a causal fill; members marked blocked/unfilled are excluded from it and
  reported as their own bucket — their later raw path (from the stored candidate bars) may
  appear in the selected-name lens only, never counted as accessible participation;
- post-fill MFE of each original member and the basket's 1st-/2nd-/3rd-largest MFE;
- frequency of days with >=2 and with 3 such excursions (right-tail clustering within the
  same basket/day);
- the same core joint-tail view for the rank-adjacent control (T9a) where a full comparison
  set exists;
- change of all of the above across the frozen timing surface.

The +H rulers here remain descriptive measurement rulers — never survivor definitions,
profit targets, or release triggers. T7's "best member pays for peers" remains one lens;
it must not be the only basket-level tail lens.

**T8 — Stability**: every headline number per month and per quarter.

**T9 — Controls**: (a) rank-adjacent strong gainers (ranks just below top-N at T) as the
primary mechanism control; (b) matched random PIT price/liquidity basket as sanity check.
Report same-day clustering caveats.

**T10 — LS materials (rule-free; THESIS §8)**: for every N=3 basket member, at every fixed
snapshot time and at every ruler-breach event, record: return from fill; gain from ranking
anchor; drawdown from own peak; MFE/MAE to date; future MFE/MAE; time to eventual session
high; share of the eventual session-max move still ahead at that moment; whether the
ultimate high already happened. Ruler-breach events are labeled **illustrative attrition
events, NOT the Phase-2 release rule**. All baskets (0/1/2 breaches) are retained so the
true event study — second-peer death under the rule frozen in PRE-REG-BASKET-02 — can be
constructed later without rerunning the anatomy.

## 6. Explicitly NOT in this pre-registration

- No release-rule or survivor-rule constants/forms (deferred: PRE-REG-BASKET-02, frozen
  after Phase 1, before Phase-2 P&L).
- No economics claims; no strategy kill rules (descriptive pass).
- No partials, adds, re-entry, dynamic replacement, or overnight trading.
- No narrowing of T/N from outcomes: all observation points and N values are reported in
  full; any later narrowing must be justified by anatomy questions in PRE-REG-BASKET-02.

## 7. Gates & sequence after Phase 1

1. Anatomy complete; full tables committed under `factory/artifacts/basket/`.
2. Interpretation strictly via the THESIS §9 hierarchy.
3. Write PRE-REG-BASKET-02: freeze ONE simple release rule + ONE permissive survivor rule +
   arms A/B/C (A: all-hold to EOD; B: release -> survivors hold to EOD; C: release +
   survivor rule) — formulated from the anatomy, frozen before any Phase-2 P&L. The
   static-survivor comparator is mandatory for any later sizing-up work.
4. One-shot inspection of the reserved unseen months (2026-06..2026-08) under the frozen
   rule; once inspected, revisions require fresh unseen months.
5. User sign-off at each gate.

## 8. Change log

- 2026-09-16: created (frozen Phase-1 measurement contract).
- 2026-09-16 (pre-extractor refinement pass): causal-eligibility clause; flag-based split
  handling (genuine news gaps kept); A_pm 09:30 first-trade fill / A_open 09:31; anchors
  per population; selection-time gain axis; AMBIGUOUS share reporting; T6/T7/T10 made
  rule-free; evidence boundary fixed (2026-06..08 reserved unseen).
- 2026-09-16 (canonical-run correction): split flags demoted to audit/sensitivity metadata
  (their signature is future-dependent — not causal admission); final-bar ladder touches
  marked non-executable (never close-substituted); anatomy regenerated from scratch.
- 2026-09-16 (timing-surface freeze, pre-aggregate): B grid expanded to the dense surface
  above (5-min open window, 15-min to 11:00, 30-min to noon) with per-T joint reporting
  (containment + remaining upside + churn + failed-ticket burden + execution realism read
  together). Frozen before any aggregate was viewed; not re-selectable from aggregates.
- 2026-09-16 (multi-survivor lock, pre-aggregate): T7b added — joint future right tail of
  the original N=3 basket (k = 0/1/2/3 members per +H ruler; 1st/2nd/3rd-largest MFE; >=2
  and all-3 day frequencies; touch vs executable lenses with blocked/unfilled members
  excluded from the accessible lens; rank-adjacent mirror; across the frozen timing
  surface). No fixed number of survivors is assumed; +H rulers remain rulers, not
  survivor definitions.
- 2026-09-16 (posture lock): §5 preamble added — discovery posture with the five practical
  signatures mapped to the frozen tables (T7b exec lenses; T5 MFE ranks; T2 remaining
  opportunity with T1 churn; T3/T4 failure-survival geometry; T4/T6 cheap-failure region);
  full reporting and no-mining restated. Contract unchanged.
- 2026-09-20 (availability amendment, SIP substrate): A_pm now computed for the full dev
  span (the 2025-only limitation was a legacy premarket-data availability artifact; SIP
  premarket tables exist for all 1,066 dev days). Population semantics unchanged.
- 2026-09-20 (post-SIP aggregate/read repair pass; none of these change frozen
  populations, timing surface, rulers, or lens definitions): T2 containment reported at the
  day level (the previous printed shares divided by a 3x event denominator); T2 remaining
  opportunity now uses the post-fill high on the same rows as the completed/ahead shares;
  T5 pre-high retracement / post-high giveback rebuilt from raw SIP prints (peak-minute
  ordering resolved, peak bar excluded) and keyed by population and T; T7 policy-free
  pay-for-team added (all-hold EOD, stylized failed-ticket costs -3/-5/-8/-10, break-even
  map); exec labeling legend added to the packet (exec = touch AND a next bar existed —
  not sellability; above = strict saleable lens); overnight shadow compounds instead of
  adds; T8 monthly/quarterly stability and T9b matched-random control regenerated on SIP;
  market base-rate funnel added (full PIT universe, both anchors, explicitly separate from
  the basket's post-entry numbers); Layer-1-vs-anatomy selection audit added.
- 2026-09-21 (advisor-directed final measurement pass; no frozen population, timing
  surface, ruler or lens definition changes): B(T) admission no longer requires a
  previous-session close (it was never part of the B ranking rule: rank = close(T-1) /
  open(09:30) - 1; prev_close is descriptive metadata only) — this removes the
  `dropped:no_prev_close` selection-audit class; open-anchored / EOD leader objects are
  recorded separately (`winners_close_open`, `winners_close_prev`) and reported as
  separate containment counters so "final top gainer" never reuses the intraday-high
  definition; `A_pm31` added as the coequal conservative 09:31-bound A_pm read; T5 path
  anatomy rebuilt trade-by-trade from raw SIP prints under the Alpaca condition policy
  (no minute ordering assumed anywhere) with strata extended to mfe>=100; T7 artifact now
  carries true monthly rollups (the previous "monthly" key held day rows, which made the
  T8 monthly pay-for-team shares invalid); Layer-2 provider fetch now retries symbols the
  bulk request silently dropped (7 of 9 previously unresolved symbol-days were recovered:
  VVPR 2022-05-13, HSON 2022-06-15, HSON 2023-07-19, CSLR 2023-09-28,
  CSLR 2023-11-13, HSON 2025-05-22, AJX 2023-07-03; PMN 2023-02-13 and MGLD 2023-09-19
  remain genuinely
  provider-empty).
  Continuation (same pass): the market base-rate artifact's canonical eligibility is now
  the $1 floor on open tradeability for both anchors (prev-close anchor additionally needs
  prev_close > 0); the previous prev-basis floor is retained as the
  `prev_close_prevfloor` sensitivity field so no published number becomes untraceable.
  New measurement views (descriptive only): market-opportunity -> basket-capture funnel
  (`capture_funnel`, both anchors, ranks at A_pm/A_pm31/A_open/B(T), post-fill remaining
  for contained monsters, and the same-day co-member fallback on miss days) and
  `race_by_view` (per-view dn-before-up shares for the (H, L) cells). The funnel was
  cross-checked against the base-rate artifact: all six (anchor, ruler) day counts agree
  exactly. T8 quarter rows now carry `pays_days`; the T8 pay-for-team shares read the
  corrected T7 monthly rollups. No parameter, population, timing point, ruler or lens was
  selected; PRE-REG-BASKET-02 remains unfrozen.
- 2026-09-21 (audit pass — measurement-layer close-out; contract clarification, not a
  semantics change): the frozen ranking rule is now explicit in code as `score descending,
  ticker ascending` at every selection point (anatomy top-K, all leader/winners lists, the
  Layer-1 candidate lists). Previously the tie-break was implicit and the engine's sort is
  not stable, so boundary ties resolved arbitrarily; with the rule stated, the Layer-1 vs
  final-anatomy selection audit is 15,990/15,990 snapshots in agreement (0 differences, 0
  promotions; the 11 previously-reported differences were exactly the 10 tie days). Also in
  this pass: T8 containment reads N=3 rows (it had been reading N=10), T8 quarterly
  economics weight by `pays_days` (filled days) rather than calendar days; T5 path anatomy
  initializes state zero at the actual fill price/time; `race_by_view` orders up-vs-down by
  raw print chronology; sub-$1-restricted leader objects (`winners_open_floored`,
  `winners_close_open_floored`) are reported alongside the unrestricted ones with their own
  containment counters; T4/T7b slot counters now count unfilled and blocked slots; the
  packet's coverage section is restored. Every headline number in the packet was reproduced
  independently from lower-level inputs by separate implementations. Two symbol-days remain
  genuinely provider-empty (PMN 2023-02-13, MGLD 2023-09-19). No parameter selected;
  PRE-REG-BASKET-02 remains unfrozen.
