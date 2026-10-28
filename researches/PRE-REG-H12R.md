# PRE-REG-H12R — RUN 1: TRUE causal top-3 market gainers (population reset)

Registered 2026-09-09, before any H12-R run. Stage: DISCOVERY — no gates; any
edge claim later requires the power doctrine (>=6mo pooled dev, >=2 pre-reg
unseen collision months, >=100bps friction). Run 2 (Ross-Cameron filter-first)
is a separate stage, only after Run 1 is understood.

## Population contract (Run 1 — no strategy filters)
- At each t in {9:45, 9:50, ..., 10:30 ET} (established H12 snapshot grid),
  rank the WHOLE universe in the market file by % gain vs previous session
  close: pc(t-1)/prevClose - 1. 1-bar lag (bars et <= t-1), scanner semantics.
- Hygiene ONLY: tradable common stock/ADR shape — exclude non-alpha tickers,
  warrant/unit/right/preferred-class suffix tickers (W/WS/WW/WT/U/UN/UNN/UT/
  UU/UW/RT/PR at len>=4; R at len>=5), common leveraged ETFs (short hardcoded
  list); require previous session close (no prev close = scanner blank);
  exclude overnight ratio outside [0.5, 2.0] (raw-unadjusted split guard —
  data hygiene, same as harness split_suspect).
- NO price band, NO bar-count minimum, NO volume/float filter. A halted-thin
  +200% name with 4 bars IS the #1 gainer if it ranks there.
- Sample = (day, t, rank in {1,2,3}). Groups analyzed: #1 / top-2 / top-3.
- Coverage caveat: "whole market" = the ~14k-ticker monthly clean_ohlcv
  backbone (project market data). Names absent from Finnhub coverage are
  invisible to reconstruction; live leaderboard sourcing (scanner/API) is a
  later, forward-observer concern.

## Representation (unchanged from H12 probes)
22 raw scalars + 120 path features (last 30 bars right-aligned, 4 channels:
bar return / range / volume-ratio / close-position-in-bar). Order and shape
preserved; NaN padding for short histories.

## Targets / economics (one deliberate change vs H12)
- Entry = open of FIRST bar with et >= t (next-bar-open fill generalized to
  halt gaps — H12 DROPPED missing-bar-t samples; halts are exactly where the
  hottest names live, so Run 1 keeps them).
- T1 = touch +4% strictly before -2% within [t, t+30] (same-bar both = 0,
  dn-first conservative). T2 = terminal return at window end. Bracket = T1-
  ordered +400/-200/window-close. Friction 100bps in econ reporting only.
- Splits: dev 2025-03..12 (LOMO), collision 2026-01..03 (unseen for this
  population). 2026-04..08 stay sealed. LightGBM fixed params, no tuning.

## Readout plan
Per group (top1/top2/top3): LOMO AUC + volrank control + bracket deciles +
deduped economics + collision same. Month-blocked tables. Interpretation
focus: does the TRUE population change the OOS story (vol-classification,
regime death) that killed the eligible-top-5 population?
