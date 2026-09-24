# INTENT — why this project exists (stable; read after AGENTS.md)

**Objective: find and exploit a profitable trading mechanism in extreme top-gainer /
momentum-stock phenomena. Realized net PnL is the only success measure.**

- Premise (working, never axiomatic): retail traders repeatedly extract money from
  top gainers in the real world; 0.82 runner name-days (+60%+ open->close; 141 over 172
  days, reconciled 2026-09-16) per trading day — 52% of days have at least one, and every
  month scanned had several. The phenomenon exists; our job is a causal, systematic,
  executable way to rent it.
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


## Research philosophy (durable; added 2026-09-24)

**Objective.** Extract as much real, executable EV as the top-gainer phenomenon actually
contains. Not a target return, not a Sharpe, not a conventional strategy template. Numbers such
as "+1%/trade" or "50%/year" are OUTPUTS, never objectives.

**The phenomenon is the asset.** Extreme momentum names repeatedly attract attention,
speculation and concentrated flows. Phase 1 measured an unusually large right tail, frequent
+30/+50/+100 post-entry excursions, and multi-survivor days. The job is to own and monetize that
- not to force it into familiar shapes.

**THE WINDOW (the core economic claim; added 2026-09-24 on owner directive).** The phenomenon is
an *early-session* explosive-attention event, not a day-long move. The morning catalyst lights it;
the climb and the climax are concentrated in the morning-to-midday hours; the afternoon is the
relaxation phase, where these names die or return to base camp. **The session close is not the value
horizon — it is the opposite side of the phenomenon.** Measuring forward value "to the EOD mark"
therefore measures the fade, and any statistic built that way is biased against the thesis by
construction.

Grounded in the project's own numbers: ≥60% runners (H019, 141 names/172 days) complete *half* their
open-to-high move by 11:25; corrected T5 for MFE≥100 main tickets puts the peak at ~180–234 minutes
after entry (≈12:30–13:20) with −19…−25% pre-high retracement and −28…−38% post-high giveback; the
A_pm sleeve's mean return is essentially flat after 11:30 while its median keeps decaying. Retail
attention and volume in this cohort are concentrated in the late morning; the biggest names peak
around midday and give back a third of the move into the close.

Consequences for how we work:
- **Default value horizon is short and intraday.** Any continuation value must be estimated at
  multiple horizons (minutes to a few hours) and reported as a *time profile*; "to the terminal
  mark" is one option among many, never the default.
- **The first question is when marginal continuation EV dies through the morning** — that time
  profile (and its state conditioning) is the object, before any trigger, level or threshold.
- **Do not bake in structure.** "Sell near the running high", "buy near a local low", "exit before
  13:00" are hypotheses to be discovered from the statistics, not assumptions to design around.
- **Ride the wave, do not ride the relaxation.** Later ownership is permitted only where the
  statistics justify it; holding into the afternoon is an empirical question, not the baseline.
- Attention/catalyst timing (when the name is being *discovered*) is a first-class state dimension,
  alongside price-path state.

**Zero fixation during discovery.** No clock time (09:30/09:45/10:00/10:30...), no profit
threshold (+30/+50/+100), no stop level, no retained-MFE fraction, no N, no one-winner
assumption, no EOD holding, no scale-out or staged-capital architecture, and no F1-F14 family
structure is a truth. They are RULERS: instruments that reveal shape. A fixed-time or
fixed-return diagnostic may reveal economics; it must never silently become the strategy merely
because it was measured. Prefer treating time, return, drawdown, MFE retention, rank, velocity
and recovery as STATE COORDINATES before they become triggers. The final strategy may be
extremely simple, but its boundaries must emerge from the phenomenon, not from inherited
constants.

**Precision where precision matters (non-negotiable).** Causal timing; only what was observable
at the decision moment; executable prices; SIP/carry integrity; independent sleeve accounting;
friction; cash and deployment constraints; exact action ordering; no future leakage; a metric
that measures what it claims; and code that actually executes the idea attributed to it. A
simulator or accounting defect can manufacture or erase EV, so these checks are never skipped.
Once the dollars are verified, do not bury discovery under generic robustness bureaucracy.

**Model-prior warning.** LLM priors come from trading books, blogs and conventional quant
practice - i.e. from average practitioners, often on phenomena unlike this one. Do not default
to "take profits at X", "use a Y% stop", "risk/reward must be Z", "most strategies fail", "that
return looks too high". When a learned prior conflicts with BASKET evidence, return to first
principles: what does this phenomenon actually do, causally, and how do we capture the most real
EV from it? Be an investigator first and an exploiter second.

**Briefs carry labelled priors, never conclusions.** When delegating analysis, a brief may state the
frame (the window doctrine), the measurement design, and priors *labelled as priors with their cohort
definition* — never a conclusion dressed as an instruction. A hindsight-cohorted statistic ("members
whose eventual peak was ≥+100% still had a median +45% of move left at 10:00") is a prior; restating
it as "the EV is before 11:00" converts a hypothesis into a finding that the analysis then confirms by
construction. This is the drift mechanism to watch: an absorbed correction gets over-learned into a
law. State priors with their definitions, and require the analysis to test them.

**Interpretation hygiene.** Separate (a) verified evidence, (b) interpretations built on it, and
(c) rulers/thresholds used to measure. Record which is which; re-test interpretations when new
evidence arrives; never cite a ruler as a conclusion; preserve disagreement between
interpretations instead of averaging it away.
