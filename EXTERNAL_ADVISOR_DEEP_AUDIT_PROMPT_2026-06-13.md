Use this exact assignment.

---

You are conducting a **deep, exhaustive, adversarial re-audit** of the Alpacatrader codebase.

Do **not** trust prior conclusions blindly.
Do **not** summarize lazily.
Do **not** stop at surface-level agreement.

You must inspect this codebase **very, very thoroughly**.
Think deeply. Work hard. Verify everything. Try to break assumptions. Look for what others missed.

## Mandatory inputs

Read these two files in full before doing anything else:

1. `EXPLICIT_REAUDIT_REPORT_2026-06-13.md`
2. `CONSOLIDATED_RESEARCH.md`

Then read the actual codebase itself.

## Your mission

You are not being asked to merely comment on the reports.
You are being asked to:

1. **Verify or refute every important claim** in both reports against actual source code.
2. **Find errors in the reports** if any exist.
3. **Find new bugs, dead paths, broken invariants, stale assumptions, race conditions, accounting flaws, state-machine holes, or hidden contradictions** that neither report identified.
4. **Understand the system at architecture level**, not just bug level:
   - scanner/discovery
   - enrichment
   - attention ranking
   - hard filters
   - move classification
   - entry detection
   - sizing
   - execution
   - protection lifecycle
   - state machine
   - exit engine
   - reconciliation
   - account risk
   - logging / auditability
   - tests / regression coverage
5. **Investigate external realities too** when they matter:
   - Finviz free delay behavior
   - Alpaca IEX/SIP feed behavior
   - any library/API/runtime behavior relevant to execution correctness

## Working style requirements

- Be **skeptical**.
- Be **forensic**.
- Be **explicit**.
- Be **evidence-driven**.
- Read code carefully and trace call chains.
- Check file:line references.
- Compare spec, code, runtime behavior, and tests.
- Look for places where the code *claims* to support something but the runtime never actually uses it.
- Look for places where local truth can diverge from broker truth.
- Look for silent failures, swallowed exceptions, fabricated data, stale data, dead states, zombie states, and accounting drift.

## Specific things you must pressure-test

### 1. Execution truth
- Can entries become locally open without real fills?
- Can exits become locally closed without real fills?
- Can partial exits collapse into full exits?
- Can a symbol get stranded in `PENDING_ENTRY`, `EXITING`, or `UNPROTECTED`?
- Can stale stop orders or conflicting orders survive after state changes?

### 2. Protection truth
- Can a position appear protected locally while no real stop exists?
- Can restart reconciliation create open but unprotected positions?
- Does the monitor truly detect missing protection, or only certain local states?

### 3. Risk truth
- Is daily P&L actually correct?
- Are realized losses preserved after position closure?
- Is unrealized P&L actually updated from market prices?
- Is `max_open_risk_pct` really enforced?
- Is per-symbol loss cap real, or just a stub?
- Can the bot continue trading after it should have hit a daily stop?

### 4. Data truth
- Where is data delayed?
- Where is data fabricated?
- Where is data silently downgraded?
- Where are values estimated instead of observed?
- Which fields are sourced from scanner vs market-data provider vs reconstructed locally?
- Are there places where raw data is overwritten and important context is lost?

### 5. Strategy viability
- Under realistic current runtime conditions, can it actually enter top gainers?
- Which setups are live in practice versus only in theory?
- Does the classifier meaningfully classify, or mostly default?
- Is the bot behaving like an execution machine or still like a disguised rejection engine?

### 6. Tests
- Which tests are precise and meaningful?
- Which tests are too broad and weak?
- Which missing tests would have caught the most dangerous regressions?
- Are there app-level integration gaps around risk, execution, and reconciliation?

## Expected deliverable

Produce a **full re-audit report** with:

1. **Verification of report claims**
   - confirmed
   - refuted
   - partially true
   - outdated

2. **New findings**
   - with severity
   - file:line evidence
   - impact
   - why it matters operationally

3. **Architecture assessment**
   - what is conceptually sound
   - what is structurally misleading
   - what should be split, simplified, or rewired

4. **Risk/safety assessment**
   - worst-case operational failure modes
   - how local state, broker state, and market state can diverge

5. **Minimal high-impact fix order**
   - if only 3 things are fixed
   - if 10 things are fixed

6. **Test plan**
   - exact missing tests
   - what each missing test would prove

## Final instruction

Do not be polite. Be accurate.
Do not be shallow. Be exhaustive.
Do not stop when the first explanation seems plausible.
Trace the system until you actually understand it.

If one of the existing audit files is wrong, say so clearly.
If both missed something dangerous, find it.

Work hard.

---
