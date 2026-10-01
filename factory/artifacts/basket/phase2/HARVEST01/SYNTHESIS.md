# HARVEST01 — synthesis (dev-only; 1,066 days; net of 100 bps unless stated)

Question: buy the actual top 1–4 gainers (split-normalized, vs previous close) at fixed
PM/RTH clocks, equal dollars, next executable open — and find the handling that makes
money. All lanes: entry = first bar open ≥ clock, gap ≥ 5 min ⇒ cash slot; exit = first
bar open ≥ max(exit_clock, fill_et+1); EOD requires a print ≥ session_end else UNKNOWN;
50 bps/side; dev days 2021-02-01..2026-05-29 (2024/2025-01 sealed; reserved untouched).

## 1. The unmanaged grid is negative everywhere

4,572 cells (variant × clock × N × exit, n ≥ 200 days). At 100 bps exactly ONE cell is
positive: raw/08:30/N=1/09:45 = +0.025% (t≈0; −0.18% at 150 bps). At 150 bps: zero
positive cells. Best primary cell −0.08% (09:29→09:31). PM entries held to the close:
−7…−9%. The naive "buy the top gainers and ride" thesis has no positive EV on dev.

## 2. Why: the money structure

Member level (78,349 filled members):
- mean post-fill MFE +24.5% vs realized −2.7% (10:00) / −4.6% (noon): the excursion
  exists; the hold gives it back.
- 2,883 members (3.7%) reach MFE ≥ +100% (mean +173%); held to noon they still realize
  +64.9% and their summed contribution (+1,872 per-slot $) exceeds the all-member total
  (−2,747): the tail pays for the team when held.
- Half the tickets (50.4%) never make +10% and average −11.6% at noon: the bleed is
  the duds, not the giants.
- Rank matters backwards at 09:20: rank1 (most extended) is the worst member
  (−3.4% at 10:00 vs −2.1% for rank4); by 11:00 the order flips.
- Giants tolerate damage: 63% never touch −10% from fill (80% never −15%, 88% never
  −20%); their median peak time is 13:18 (duds peak at 10:00).
- The tail is accessible: at 09:20 the top-3 contains the day's session-max leader on
  52.8% of days, and 38% of all +100% session-max names; joint tail after the actual
  fill: 54% of days have ≥1 member touching +30%, 13% have ≥1 touching +100%.

## 3. Handling: positive increments, still negative absolute

Member rules (basket-day delta vs holding to the same endpoint, primary N=3, pooled
across clocks; both blocks positive):
- gb10 (peak giveback) +1.7 pp — the strongest, at every clock (t up to 7.6)
- gb10_half +0.85, dmg_wait +0.74, decay_v +0.62, decay_nh +0.60, tstop30 ≈ +0.5,
  failrec_a3 +0.4
- re-entry (reclaim same name) −0.44 pp; replacement with the current leader −0.46 pp:
  both NEGATIVE in both blocks — moving released money back into the race destroys value.
The giant-kill accounting explains the ranking: gb10 fires on 99.9% of the 2,883 giants
(a giant almost always gives back ≥10% from some peak) and costs them −51% per dollar;
dmg_wait fires on 20.4% (−10.6%), failrec_a3 16.4% (−8.7%), decay_v 14.2% (−6.1%),
tstop30 10.1% (−3.2%). The new families genuinely "spare the monster" — but they also
cut fewer duds, so their net increment is smaller.
The 36-policy grid agrees: sell-into-strength partials are negligible (touch30_33
+0.07 pp, touch50_33 +0.05 pp, touch100_33 +0.05 pp, touch50g10 +0.01 pp pooled at
noon) — the giants keep running past the partial exits; gb10 remains the single best
rule at every endpoint (+4.0 pp at the close). Best absolute combination anywhere in
the grid: 11:00 → 12:00, N=1, damage+wait = −0.80% (member rules: −0.74%).
Everything else ≤ −0.85%. The increments are real and block-stable; they are not
enough to reach zero.

## 4. What deserves the next serious confirmation

1. The containment/tail facts (53% of session-max leaders in the 09:20 top-3; 54% of
   days with a +30% touch) — these are the strongest empirical assets found; a
   pre-registered measurement on reserved months should confirm them before any
   strategy work builds on them.
2. The "spare-the-monster" release family (tstop30/decay/failrec at −15/−20% damage
   thresholds rather than −10%) combined with exits into strength (partials at +50/+100)
   — the policies grid quantifies the parts; the combination is the open question.
3. The only near-zero absolute structures are very short PM scalps (09:29→09:31/09:35,
   gross ≈ +0.9% before friction) — adjacent to the live H025 flush-bid mechanism, not
   to the basket thesis; they do not justify 100 bps friction as tested here.
NOT to be resurrected: raw (unfiltered) selection (43% of PM raw rows are broken
prev-closes), re-entry, replacement, hold-all-day.

## 5. Pending at draft time

- Policy grid (36 rules incl. sell-into-strength partials + trails) — running.
- Basket capital lane (release→cash / survivors / leader / reserve / dip) — running.
- Golden-window birds-eye readout — running.
- Pass 2 (compact-anchored variant across all lanes) — auto-starts after pass 1.
