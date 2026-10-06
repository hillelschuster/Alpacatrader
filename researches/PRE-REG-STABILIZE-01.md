# PRE-REG-STABILIZE-01 — post-flush confirmation entry (development study)

Registered 2026-10-06, BEFORE running. Context: EXP-92 resolved the frozen H025
formulation NEGATIVE (same-bar target credit was ~89% pre-fill spikes). This study
tests a different object: do NOT catch the falling knife; wait for the flush to
stall/reclaim and buy the recovery with honest timing.

## Formulation (exact semantics = factory/scripts/h025_research_stabilize.py)

1. Qualification (same causal state as H025): top-3 rank, gain >= 1.0,
   pullback >= -0.01, r15 >= 0.03 (rolling state minute; reference c0 = state close,
   B = 0.9*c0; reference expires 120 min after its state minute). Optional ablation:
   prior-flush persisted counter (pf_carry) >= 2.
2. Flush: any bar (label > state minute) with low <= B while flat. The reference
   FREEZES at the flush (B, c0 kept as the broken reference; fresh rolling state
   resumes only when flat with no active setup).
3. Confirmation on a later completed bar nb (low > B, no new flush): variants
   - "stall":   close(nb) >= close(latest flush bar)
   - "reclaim": close(nb) >= B (bid level reclaimed)
   - "half":    close(nb) >= flush_low + 0.5*(B - flush_low)
   - "none":    control, enter at the first open after the flush bar (no confirmation)
4. Entry: OPEN of the bar immediately following nb (for "none": bar following the
   flush bar). Degenerate setups (stop level >= entry price) are skipped and counted.
5. Exit from the entry bar onward, stop-first within every bar; gap stop exits at
   min(open, stop):
   - target variants: c0 * {1.00, 1.02} (high touch, at/after entry bar)
   - stop variants: {flush_low, entry * 0.94}
   - hold: 60 new bars -> close; EOD -> close.
6. Friction 100/150/200bps; integer orders floor(N/px) at N in {250,500,1000}.

## Scope and gates

- Data: the 1046-day allowed calendar (533 original + 513 seen replication); all
  dates already H025-seen — DEVELOPMENT ONLY, no OOS claim. 2024/Feb-2025/Jun-Aug-2026
  outcomes excluded.
- 32 bounded policy cells (4 confirms x 2 targets x 2 stops x pf {any, >=2}).
- Selection discipline: report EVERY cell with per-fill mean, fills/day, total net
  $/day (zero-filled), by block/year, worst fill, occupancy. Prefer plateau/neighborhood
  and cross-block sign stability; no best-cell hunting; no legacy-convention credits
  exist by construction (entry at a future open; exits stop-first).
- Kill: if no cell shows positive net $/day at 150bps with consistent sign across
  both blocks and years -> the object is reported negative and closed.
- Fresh test: a frozen winner would go to the forward shadow (IEX, non-trading) after
  an explicit freeze; NOT this run.

## Known limits (declared before results)

- Minute-bar exits still carry within-bar stop/target ordering ambiguity on bars
  AFTER entry; stop-first is retained (conservative). Print-level (NET) refinement is
  possible for finalists.
- No queue model; entries are market-at-open; size beyond a few $100 unverified.
- Reference freezes at flush; a different (rolling-updating) reference is a different
  formulation and is not covered by this registration.
