# HARVEST01 — what was tested, exactly (written spec; dev-only)

This file is the precise, auditable specification of everything the night's runs
measured. It exists so "we tested X and it failed" can never drift into "the thesis
is dead". Data span: 1,066 development days 2021-02-01..2026-05-29 (2024 and 2025-01
sealed; 2026-06..08 reserved, untouched). All results net of 50 bps per side
(=100 bps round trip) unless a row says otherwise; 150 bps stress computed where noted.

## 1. Selection (what "top gainer" meant)

- Universe: PIT listed common stock (pit_symbols latest vintage <= day), price > 0.
- Metric: split-normalized gain vs the immediately previous session's RTH close:
  `gain = px / (prev_close * prod(old_rate/new_rate over splits with prev_session < ex_date <= day)) - 1`.
  3,630 reverse splits + 565 forward splits in span; without normalization a sampled
  reverse split showed a fake +468% vs true ≈ −5%.
- Decision clocks (ET minutes): PM **510, 540, 550, 555, 560, 565, 569**;
  RTH **571, 575, 580, 585, 590, 595, 600, 615, 630, 660, 690, 720**.
  Decision price = last completed bar strictly before the clock (PM: close of last
  premarket bar et <= clock-1; RTH: race.minute_full row t=clock, whose px is the
  close of the last bar et <= clock-1, known_by_t true).
- Variants: `raw` (all rows with a price); `primary` (raw minus rows flagged
  prevclose-discrepancy / nonpositive / gain>10x / px<$0.05); `listed` (primary with
  pit tag); `compact` (anchor = previous session's rth-compact c_last, same lane the
  legacy A_pm used). 43% of PM raw rows are discrepancy-flagged; primary is the
  defensible ranking anchor (independent audit).
- Top N ∈ {1,2,3,4} by (gain desc, ticker asc).

## 2. Entry / exit conventions (what "buy" and "sell" meant)

- Entry: buy each selected name at the OPEN of the FIRST minute bar whose et >= clock
  (the bar open = that minute's first trade). If the first bar comes >= 5 minutes late,
  or never, the slot stays in cash for that day (blocked slot, never backfilled).
- Exit at a fixed clock E: sell at the open of the first bar with et >= max(E, fill_et+1).
  E ∈ {555…955} grid + session_end; for E = session_end a bar printed at/after
  session_end is required, otherwise the member is UNKNOWN and the cell excludes that day
  (never zero-imputed).
- Costs: buy price × (1+0.005); sell price × (1−0.005) [0.0075 for the 150 bps stress].
- Basket: equal dollars per slot (1/N each); blocked slots are cash; the basket-day is
  the unit of account.

## 3. The management families actually tested (all causal, next-bar-open execution)

Member rules (12): `gb10` (close <= 0.90×running high); `dmg_wait` (close < fill×0.90,
still below 5 min later); `failrec_a3/a5/a8` (damage >10%, bounce ≥3/5/8% off the
episode low, then close below the episode low); `decay_v` (damaged 10% AND 5-bar volume
mean < 0.5× prior 20-bar mean); `decay_nh` (same + no new high in 20 bars); `tstop30`
(below −10% for 30+ min without a +5% reclaim print); `gb10_half`, `failrec_a5_half`
(sell half); `reentry_gb10` (re-buy after a close ≥ the sell price); `replace_gb10`
(sell, buy the rank-1 current market leader at the next 5-min clock).

Policy grid (42): giveback {10,15,20,25}% × sell {100,50,33}%; dmg_wait {10,15}% × {100,50};
failrec {3,5,8}% × {100,50}; decay ratio {0.5,0.3}; nohigh-20-damaged-10; tstop;
**cadence**: stall{10,15,30} (no new running high in T bars) and nohigh{10,15,30}d0
(same AND close ≤ fill); touch{30,50,100}% × sell 33% (scale-out into strength);
touch50-then-giveback10 (trail); plus reclaim_same and rotate_leader variants of
gb10 / failrec_a5 / decay_r50.

Basket capital (14 policies): base_hold; release-trigger {gb10, failrec_a5, decay_r50,
nohigh20d10} × destination {cash, equal survivors, best survivor, market leader};
reserve scale-in family (2/3 deployed at entry, 1/3 held back): deploy on first member
touching +20% / +50%, deploy at 10:30 into all members ≥+20% by then, deploy on the
first member dipping ≥10% / ≥15% from its running high, or never (reserve_cash).

## 4. Results (headline, net-100 unless stated)

- Unmanaged grid: 4,572 cells with n≥200; exactly ONE positive cell (+0.025%, raw
  08:30→09:45, t≈0; negative at 150 bps). Best primary cell: 09:29→09:31 −0.14%,
  09:29→09:35 −0.08% (gross +0.86% / +0.92%). PM→close −7…−9%.
- Member rules: all deltas positive vs hold and block-stable (gb10 +1.71pp pooled,
  best clock t=7.6); best absolute −0.74% (dmg_wait, 11:00→12:00, N=1). gb10 fires on
  99.9% of the 2,883 giant members (costing them −51% per dollar); tstop30 on 10.1%.
- Policy grid: stall10 is the best pooled delta (+2.18pp) but fires on 99.9% of giants;
  touch-partials ≈0; best absolute −0.46% (nohigh10d0, 11:00→12:00, N=1).
- Basket capital: best absolute −0.53% (reserve scale-in, 11:00→12:00, N=1); deltas
  positive (+1…3pp) but never crossing zero; re-entry/replacement negative.
- Money structure (member level, 78,349 fills): mean MFE +24.5%, realized −2.7% at
  10:00 / −4.6% at noon; 3.7% of members reach MFE ≥100% (mean +173%, realizing +64.9%
  at noon); 50.4% never make +10% and average −11.6% at noon; giants' median peak
  13:18; P(giant | touched −10%) = 2.0% vs 6.9% if never damaged.
- Containment: at 09:20 the top-3 holds the day's session-max leader on 52.8% of days
  and 38% of all +100% session-max names; 54% of days have ≥1 member touching +30%
  after the actual fill (13% ≥1 touching +100%).

## 5. What this does NOT cover (scope of the negative)

- Only these entry/exit/handling formulations, on these clocks, with these cost
  assumptions, on this dev span. No sub-minute or order-level execution; no filters
  other than "top gainer"; no combinations of rules as a single policy; no shorting;
  no sizing; no state-conditioned V(t) discovery (the next move).
- The 100 bps friction is a project RULER, not a measured property of these names;
  direct execution measurement is the designated next step for the near-zero cells.
