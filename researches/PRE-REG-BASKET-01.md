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
missing 09:30 bars per month; split-suspect rate on candidate days; duplicate
(timestamp,ticker) rate; symbol-reuse checks; premarket print staleness (2025); candidate-day
bar coverage; half-day sessions. Execution-realism sampling: pull SIP trades/quotes for a
small predeclared sample of candidate name-days (lb18_fills_micro pattern) to bound spread/
slippage where feasible.

## 2. Universe — broadest defensible; strata reported

PIT listed common stock (NYSE/NASDAQ/AMEX), price >= $1 at decision time; >=100 session
bars; split-suspect days excluded via the existing [0.5,2] overnight-ratio guard. Do NOT
inherit another strategy's screen ($2/$5M certify filters, runner $1-50 band) as a
pre-selection. Report every table stratified by price band, dollar volume, session-bar
count, halt count, and warrant/penny flags where inferable. The executable Phase-2 universe
is frozen later for operational reasons only — never because a band produced attractive P&L.

## 3. Populations (A and B coequal; overlap retained)

- `A_open`: rank by open(09:30 bar)/prev_close - 1; decision bar 09:30; fill 09:31 open.
  (09:30 open = first RTH trade; no official auction print exists in this data.)
- `A_pm`: rank by last premarket print <= 09:30 / prev_close - 1; declared freshness rule
  (default: print within the last 15 minutes before 09:30; staleness sensitivity reported);
  2025 only — never pooled with A_open.
- `B(T)`: rank by close(last completed bar, et <= T-1) / open(09:30) - 1; fill at first bar
  open after T; T in {09:45, 10:00, 10:30, 11:00}.
- N ladder: 1/3/5/10 descriptive. Fewer than N qualifiers = fewer slots; unfilled slots are
  cash; empty days remain in denominators. A∩B overlap is measured and reported, not
  excluded.
- Rank turnover: full churn matrices P(i in topN(T') | i in topN(T)) and new-entrant shares.

## 4. Rulers (descriptive only — never targets or definitions)

- Upside ladder from fill: +5/+10/+20/+30/+50/+100%. Adverse ladder: -3/-5/-8/-10/-15%.
- Both touch-based (minute extreme) and conservative executable (next-bar-open after
  threshold) forms where applicable. A minute-high touch is an upper bound, never a capture.
- Same-bar both-touch: flag AMBIGUOUS; report optimistic and pessimistic bounds; use
  sub-minute evidence where available (`data/subminute/*_exit_quotes`; sampled SIP) — never
  silently decide. Adverse-first is allowed only as a labeled stress bound for execution
  simulation, not as observed truth.
- Friction presentation for any economics-flavored table: gross; realistic executable
  estimate (sampled); 100bps stress; 150bps stress. Stress testing is required; deliberate
  pessimism is not measurement accuracy.

## 5. Tables (all month-blocked; full reporting; no T/N mining)

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

**T6 — Structural realism buckets**: clean vs gap-through fills; halted-at-entry excluded;
halted post-entry = reopen execution (flagged uncertain; no LULD flags in data); unfilled
slots; involuntary overnight carries (halted through EOD).

**T7 — Basket-level descriptive**: ex-post pay-for-team (P(best member pays for all others);
mean surplus/deficit); break-even map (r* = k*lambda intuition + executable version); fixed
overnight shadow ledger (EOD -> next executable open; diagnostic only — never used to repair
the intraday result).

**T8 — Stability**: every headline number per month and per quarter.

**T9 — Controls**: (a) rank-adjacent strong gainers (ranks just below top-N at T) as the
primary mechanism control; (b) matched random PIT price/liquidity basket as sanity check.
Report same-day clustering caveats.

**T10 — LS anatomy columns** (descriptive; THESIS §8): at second-peer death in N=3 baskets,
for survivor C: return from fill; gain from ranking anchor; drawdown from own peak; MFE/MAE
to date; future MFE/MAE; time to C's eventual session high; percentage of eventual
session-max move still ahead; whether the ultimate high already happened. All N=3 baskets
with >=2 deaths included.

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
4. User sign-off at each gate.
