# THESIS — BASKET-01: participation / survival selection (Phase 0 artifact)

Status: PHASE 0 — documentation only. No strategy code, no runs, no release-rule constants.
Created 2026-09-16. Companion: `researches/PRE-REG-BASKET-01.md` (Phase-1 measurement
contract), `researches/STATE.md`. H025 remains independent and untouched.

---

## 0. The invariant thesis

We buy participation in a small race of plausible extreme leaders. We do not know the
winner at entry. The market is allowed to eliminate candidates. Failed tickets should
become inexpensive. We preserve meaningful exposure to the candidate that keeps
surviving. We impose no predetermined profit destination. If peers die while one candidate
remains strong, that relative survival may itself contain information and may eventually
justify reallocating some released risk toward the survivor under a fixed portfolio-risk
ceiling.

Compact form: **diversified uncertainty -> market-driven elimination -> preserved
survivor -> potentially concentrated evidence.**

## 1. The creed (verbatim)

> We buy participation in the race. We do not know the winner at entry. We allow the
> market to eliminate candidates for us. We try to make eliminated candidates inexpensive.
> We protect and support the surviving candidate, because the exceptional survivor is what
> can pay for the participation tickets. If other candidates die while one survives, that
> relative survival itself may later justify increasing our support for the survivor.
>
> Like a father helping a child still running after the others have fallen: we do not
> carry him across the finish line or invent success; we simply avoid abandoning him while
> he continues demonstrating that he can run.

## 2. Formal statement

- Universe U(d): PIT listed common stock, broadest defensible screen; stratification is
  reported, not selected (see PRE-REG §2). The executable Phase-2 universe is frozen later
  for operational reasons — never because a band produced attractive P&L.
- Population A (coequal): `A_open` ranks from the completed 09:30 bar by open(09:30 first
  RTH bar)/prev_close - 1, fill at the 09:31 open (1-bar lag); `A_pm` ranks by last
  premarket print <= 09:30 / prev_close - 1 (known before the bell; 2025 only; freshness
  rule), fill at the 09:30 bar open — the first realistic RTH participation opportunity
  (conservative bound: 09:31 open). Population B (coequal): ranks by close(last completed
  bar, et <= T-1) / open(09:30) - 1 at T in {09:45, 10:00, 10:30, 11:00}, fill at the
  first bar open with et >= T. Anchors stay per-population (A = prev close, B = RTH open);
  the other anchor is descriptive only.
- Intended basket S_d(T) = top-N, N in {1,3,5,10} (descriptive ladder); fewer qualifiers =
  fewer slots; unfilled slots are cash and remain in denominators; empty days remain in
  denominators. A name halted at the entry window is a blocked slot, never a silent
  exclusion; selection-time gain is recorded as a descriptive axis, not a filter.
- Policy pi uses causal rules only: a release rule, a survivor rule (both frozen in
  PRE-REG-BASKET-02, after Phase 1), and a forced-flat EOD procedure.
- Basket return `B_d(pi)` = mean of slot returns, net of >=100bps baseline friction
  (150bps stress). The basket-day is the unit of account.

Three questions — never conflated:

1. **IDENTIFICATION** (diagnostic): did the basket contain the eventual session champion?
   Missing the champion is a missed-opportunity diagnostic, NOT a thesis failure. A basket
   of A=-4%, B=-3%, C=+30% can be excellent without containing the +150% name.
2. **ECONOMICS** (the real test): did the basket contain enough *accessible* right tail
   for its best/surviving member to pay for the failed tickets?
3. **PORTFOLIO VIABILITY**: the full basket-day distribution after friction, gaps, halts,
   unfilled slots, forced-flat, month-blocked.

## 3. State semantics (conceptual state machine — no code)

```
CANDIDATE -> ACTIVE SURVIVOR -> RELEASED
ACTIVE SURVIVOR -> LAST SURVIVOR                    (when applicable)
LAST SURVIVOR -> ADD-ELIGIBLE SURVIVOR              (only if LS evidence supports; deferred)
All paths terminate in RELEASED or FORCED FLAT.
```

There is **no state "RUNNER because +30%"**. A survivor is defined by continued
satisfaction of the causal participation rules — not by reaching a predetermined gain.

**We do not pre-decide how much the survivor is allowed to make. We eventually pre-decide
what observable behavior causes us to stop participating.**

Consequences: no final profit target anywhere. Complete liquidation happens only on
(a) release rule fire, (b) forced-flat (EOD procedure), (c) a declared "no longer worth
participating" rule. Partial realization is an architecture variant for Phase 3 — never a
default. Wide-giveback examples (peak +150 -> exit +100; peak +50 -> exit +30) illustrate
permissiveness; they are not targets.

## 4. What this is not (non-goals)

- Not stock selection or prediction. Rank identification is dead here (H12 retired;
  dominant-leader watchlists ~0). This thesis does not resurrect it.
- Not ML; no reinforcement-learning framing.
- Not generic momentum harvesting; not per-name win-rate/expectancy optimization.
- No pre-decided runner threshold. The +5/+10/+20/+30/+50/+100 ladders are **rulers**
  (PRE-REG §4), not definitions of a runner and not sell targets.
- Intraday is the primary horizon; EOD is forced-flat; the overnight shadow ledger is
  diagnostic only. An overnight thesis would require its own pre-registration.
- No dynamic replacement, adds, re-entry, or exhaustion modeling yet — documented branches
  only.

## 5. Labels (retrospective, descriptive only)

Four distinct objects, never mixed:

1. **Session-max leader** — largest intraday excursion from RTH open; rank #1/#2/#3.
2. **Terminal/EOD leader** — largest open->close (or prev-close->close) gain.
3. **Capturable post-fill excursion** — measured from OUR executable fill, per candidate.
4. **Eventual session-max membership** of our basket at T (containment, paired with the
   share of the move still ahead of our fill).

A day of open $2 / high $5 / close $3 is an extraordinary monster day; open->close
definitions alone would miss it. Ladder values are measurement rulers only.

## 6. Pay-for-team — two concepts, never conflated

1. **EX-POST pay-for-team**: did the eventual best member of the ORIGINAL basket have
   enough profit to pay the other members' losses under explicitly labeled stylized cost
   scenarios (all-hold-to-EOD member outcomes; ruler-based failed-ticket costs)? Tests
   whether the raw basket population contains the economics. (Ex-post anatomy — the best
   member is known only afterward; never a per-trade goal. Phase 1 computes only this
   form.)
2. **POLICY pay-for-team**: did the member our causal survival rules actually kept exposed
   generate enough realized profit to pay the other members' losses? Tests whether the
   architecture harvested the opportunity.

## 7. Break-even tail map

Intuition (equal notional): k released members at realized loss `lambda` => the survivor
must return `r* = k * lambda` for the basket to break even. The executable version uses
actual basket economics — realized peer losses + friction + slippage/gap costs + relevant
basket costs — and asks whether the causally held survivor reaches that required profit.
Phase 1 reports the policy-free illustration only (all-hold / ruler scenarios); the
executable policy version exists only after the release rule is frozen in
PRE-REG-BASKET-02 (there is nothing meaningfully "held" before a rule exists).

## 8. Last-survivor hypothesis (recorded; deferred; logging-only in Phase 2)

**Hypothesis LS**: conditional on C's own observable state, does the death of its original
peers increase C's subsequent right-tail opportunity? If yes, cross-sectional attrition
itself contains information; if no, "size up the last survivor" may merely be late
winner-chasing.

**Event** (N=3): the moment the SECOND peer dies while C survives — defined by the release
rule frozen in PRE-REG-BASKET-02 (before that rule exists, "death" has no realized
meaning). Record for C at that moment: current return from fill; gain from ranking anchor;
drawdown from C's own peak; MFE/MAE to date; future MFE/MAE; time until C's eventual
session high; percentage of C's eventual session-max move that still lies AHEAD; whether
C's ultimate high had already happened. This is essential — LS is useful only if peer
attrition tends to occur while meaningful opportunity remains. Phase 1 therefore records
the rule-free materials (per-member state at fixed times and at illustrative
ruler-breach events) so this event study can be constructed later without rerunning.

**Design**: strata by peer-death count (0/1/2 at matched times), controlling C's own state
(gain since fill, time, MFE/MAE to date, drawdown from peak, rank, halt count, liquidity,
day breadth). Phase 1 records materials only (no optimization of "close in time"); the
event study runs once the rule is frozen; a causal add-policy test against a
static-survivor comparator requires 6+2 power later.

**Sizing-up** (deferred to Phase 4, gated on LS): reallocate released risk budget under a
fixed account-level gap-risk ceiling. Permanent accounting distinction: **cash/principal
recovered is not the same as economic risk removed** — a "house money" position still has
real marked-to-market value. Track separately: realized cash; current marked exposure;
capital released by dead peers; nominal stop risk; conservative gap-adjusted risk; locked
profits. Comparator: peers die -> C unchanged, versus peers die -> causally add under
fixed portfolio-risk rules.

## 9. Evidence hierarchy (what challenges what)

1. Data/implementation contradiction -> **AUDIT AND RECONCILE** (producer -> artifact ->
   calculation; end with one canonical truth).
2. One T/N/rule parameterization -> **PARAMETERIZATION EVIDENCE**.
3. One payoff architecture -> **ARCHITECTURE EVIDENCE**.
4. A-vs-B difference -> **POPULATION EVIDENCE**.
5. Static basket misses later-emerging leaders -> **DYNAMIC PARTICIPATION branch**,
   triggered only if Phase 1 shows frequent later emergence with substantial remaining
   upside.
6. **THESIS-LEVEL** only if: the candidate population lacks future executable right tail;
   true monsters routinely require adverse excursions so large that losers cannot be made
   meaningfully cheaper; achievable winner tails cannot pay co-member losses; the apparent
   tail exists only in non-executable marks; or even a generous executable upper bound
   cannot make the basket work.

Discipline: verify machinery before interpreting — in both directions. Power doctrine is
binding: >=6 months pooled dev + >=2 pre-registered unseen collision months for any
selection/timing claim; collisions are one-shot; never resurrect a failed formulation.
Evidence boundary (fixed before Phase 1 begins): 2026-06..2026-08 are reserved unseen for
BASKET-01 — excluded from Phase-1 anatomy and from Phase-2 rule formulation, inspected
one-shot under the frozen Phase-2 rule; everything else is development; long-run
validation is forward.

## 10. Collisions, relatives, boundaries

- **H2** (archived, planned but never executed): "enter top-5 @10ET mechanically, all
  intelligence in stay-rules" is the closest historical relative. BASKET-01 is a distinct,
  powered, pre-registered formulation (basket accounting, A+B coequal, anatomy-first,
  release/survivor rules frozen after Phase 1). Recorded here so it is never silently
  re-run as new.
- **H12** (top-5 panel) and **H11** (afternoon rental): retired; do not resurrect.
- **Dominant-leader program** (`researches/07-...`, frozen): historical context only.
- **H025 flush rule**: physically untouched — separate account, dirs, pre-reg; no shared
  mutable state.
- **ML top-gainer lane** (`factory/artifacts/ml`): shares observer selection rules — no
  edits there.
- Reuse/block audit and data limits: PRE-REG-BASKET-01 §1-2.

## 11. Measurement contract and data reality

Contract (binding): ET clocks, causal-only, 1-bar lag, open-anchored gains, conservative
same-bar DD-first for execution; next-bar-open fills; honest friction (100bps+); month-
blocked reporting; pre-registered kills.

Data limits to respect: premarket bars **2025 only** (Alpaca SIP, 04:00-09:29:59 ET); no
official 09:30 auction print (the 09:30 bar open is the first RTH trade); no LULD halt
flags (halts inferred from >=5-min bar holes); raw/unadjusted prices with the [0.5,2]
split guard as exclusion; clean bars 2021-2023 + 2025-02..2026-02, backfill 2026-03..08;
leaderboard/path tape 2021-02..2026-08; no stored NBBO (partial `data/subminute/*_exit_quotes`
plus on-demand SIP pulls).

## 12. Change log

- 2026-09-16: created. Incorporates the final conceptual corrections: no pre-decided
  runner threshold; no profit target; state semantics (CANDIDATE / ACTIVE SURVIVOR / LAST
  SURVIVOR / RELEASED / FORCED FLAT); identification-vs-economics separation; ex-post vs
  policy pay-for-team; four label objects; LS hypothesis with attrition-timing measures;
  cash-vs-risk accounting; executable break-even map; A/B coequal; broad PIT universe with
  reported strata; H019 corrected frequency (0.82 genuine runners/day; fallback rows are
  not runners).
- 2026-09-16 (pre-extractor refinement pass): causal-eligibility hardened (no post-decision
  fields in selection); A_pm fills at the 09:30 first-trade open, A_open at 09:31;
  per-population anchors explicit; split handling made flag-based so genuine large news
  gaps are not deleted; AMBIGUOUS share reported; Phase-1 pay-for-team/break-even/LS forms
  labeled rule-free; evidence boundary fixed (2026-06..08 reserved).
