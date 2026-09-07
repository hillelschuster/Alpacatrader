# INTENT — why this project exists (stable; read after AGENTS.md)

**Objective: find and exploit a profitable trading mechanism in extreme top-gainer /
momentum-stock phenomena. Realized net PnL is the only success measure.**

- Premise (working, never axiomatic): retail traders repeatedly extract money from
  top gainers in the real world; ~0.8 runner name-days (+60%+ open->close) per trading
  day — roughly half of trading days have at least one, and every month scanned had
  several. The phenomenon exists; our job is a causal, systematic, executable way to
  rent it.
- Research stance: profitability decides everything. Simple > sophisticated when
  simple captures the money. No template worship; follow evidence (AGENTS.md principle).
- Evidence standard: ET-anchored causal harness, one-bar lag, next-bar-open fills,
  pre-registered kill rules, month-blocked reporting, honest friction (100bps+ for
  these names). Dev positives are hypotheses until they survive collision months.
- Two workstreams, one goal:
  - RESEARCH (factory/, researches/): discover and validate the mechanism.
  - BOT (src/, config/, tests/): paper-trading apparatus rehearsal — only relevant
    once research produces a validated edge. Do not confuse their states.
- Non-negotiable discipline learned the hard way: **never resurrect a formulation
  that failed a pre-registered collision**; the only path back is a genuinely
  different formulation with more statistical power, pre-registered again.
