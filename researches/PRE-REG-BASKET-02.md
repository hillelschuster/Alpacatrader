# PRE-REG-BASKET-02 — Phase-2 family-level implementation contract

**Status: FROZEN 2026-09-22.** This is the family-level Phase-2 specification: it freezes the
*permitted implementation families and finite grids*, not a final strategy. The final
(single) strategy freeze moves to the next owner gate (Phase 3). This document supersedes the
2026-09-17/18 draft entirely — that draft tried to freeze ONE release rule (T\*=600, L=10,
g=50, N=3) against legacy numbers; its constants are hereby demoted to **legacy owner
decisions, not results**, and are not binding on Phase 2.

Companions (normative): `researches/THESIS-BASKET-01.md` (creed, state machine,
multi-survivor lock), `factory/BASKET-SIM-CONTRACT.md` (event-driven simulation mechanics —
every Phase-2 run uses it), `factory/artifacts/basket/sip/READ_PACKET.md` (Phase-1 read).
Owner directive (2026-09-22, verbatim intent): proceed with the family-level PRE-REG-02,
freeze it **before strategy outcomes are inspected**, then run the parallel implementation
research and return the strategy landscape.

Naming lock: **populations** A_pm / A_pm31 / A_open / B(T) (selection identities);
**families** F1…F14 (research workstreams); **modules** R\*, S\*, C\* (release / scale /
capital); never reuse legacy Arm-0/1/2 names.

---

## 0. Purpose and working mode

1. Phase 1 established a reproducible, strongly right-tailed top-gainer population on 1,066
   SIP development days (packet: containment B/600 top-3 20.8%, joint touch +30 k≥1 ≈ 39–44%
   by entry family, matched-random +30 ≈ 1.65%). Phase 2 is **implementation discovery**:
   find the highest-EV causal way to harvest the measured phenomenon.
2. **Signal definition vs execution implementation are separate layers.**
   *Signal definition* = selection time & anchor, state variables, release/strengthening
   conditions (economic semantics). *Execution implementation* = fills, gaps, blocked slots,
   action sizes, capital plumbing, friction, forced flat (mechanics; frozen in the sim
   contract). A family may change its signal while retaining identical execution semantics —
   comparable. Any change to execution semantics is a contract-level DoF (§4).
3. The development set is allowed to teach the implementation (families, thresholds inside
   the frozen grids, gates). Untouched evidence stays untouched (§1).
4. The program is neither a rejection gauntlet nor a confirmation machine: unfavorable cells
   are reported with the same prominence as favorable ones, and real defects are surfaced
   immediately.
5. **N=3 was a Phase-1 measurement convention, never an established economic choice.** N is
   a measured family (§3.2). No Phase-2 summary may silently convert "top-3 was measured"
   into "the strategy is N=3". Likewise no constant in this document is a result.

## 1. Evidence boundary and data

- Substrate: canonical SIP artifacts under `factory/artifacts/basket/sip/` (1,066 dev days,
  QA PASS, 159,900 candidate rows, 15 snapshots/day).
- **Development span**: 2021-02-01 … 2023-12-29 + 2025-02-03 … 2026-05-29.
- **Sealed**: 2024 + 2025-01 (certified mechanically, never computed on) — remain sealed
  through Phase 2. **Reserved**: 2026-06/07/08 — never fetched, never read. Holdout
  inspection happens only at a later, explicitly gated one-shot under a frozen Phase-3
  strategy. The sim loader must refuse any day outside the dev span.
- Phase-1 artifacts are read-only for Phase 2; no re-litigation of the measurement layer.
- Session calendar: `factory/artifacts/basket/sip/phase2_session_calendar.json` (built and
  verified mechanically before any Phase-2 P&L; early closes = 779, else 959).

## 2. Canonical simulation contract (normative reference)

Every Phase-2 run uses `factory/BASKET-SIM-CONTRACT.md` literally. Non-negotiables:
anatomy fills (never recomputed), 1-bar lag decisions, next-bar-open executions, blocked
slots = cash retained in denominators, `min(open, level)` adverse convention for level-type
exits, forced flat at the session's last bar open, pending-through-halt involuntary carry
(no leverage, slot kept), friction per side = bps/2, base 100 bps round trip (adversary
150), capital normalized per run (`C0 = 1` per basket-day, equal slot budgets), realized vs
marked separated, deterministic event precedence, standard metric table, month-sharded
resumable outputs. Changes to the contract invalidate comparability and are DoF §4.

## 3. Frozen family registry (permitted Phase-2 work)

Grids below are **finite and closed**. Anything outside them is a new DoF (§4). Every run
reports its **entire surface**, never only the best cell, with the signal/execution layers
labeled.

### 3.1 F1 — Entry families and basket breadth (baseline engine)
- Entry families: `A_pm`, `A_pm31`, `A_open`, `B585`, `B600`. `B575`/`B615` are permitted
  **neighborhood stability points only**. No other anchor or T is a Phase-2 entry family.
- N ∈ {2, 3, 4}, equal total capital across N (C0 fixed; slot = C0/N).
- Primitive exits: hold-to-flat; `R1(L)` with L ∈ {8, 10, 15} (primary 10/15).
- Mandatory output: marginal expected-tail capture per additional slot vs marginal
  failed-ticket cost; earlier entry (more uncertainty) vs later entry (resolved identity)
  breadth question measured, not assumed.

### 3.2 F2/F12 — Golden-window state read (09:40–10:15) and state panel
- Checkpoints: et ∈ {580, 585, 590, 600} primary, {615} permitted. Nothing else.
- Feature vocabulary (causal, by checkpoint only; computed from bars + snapshot):
  return from fill; return from prev close; return from 09:30 open; current rank; rank
  change since premarket; rank stability; MFE so far; MAE so far; drawdown from running
  high; time since running high; position within current H/L range; velocity (1/3/5 min);
  acceleration proxy; cumulative volume; dollar volume; volume acceleration; new-high
  count/frequency; time spent below recent high; bar/trade persistence; recovery/reclaim
  structure; spread state where quote coverage exists; relative strength vs other basket
  members; basket score dispersion; basket breadth (race state).
- Cohorts (not just H100 winners): remaining-tail strata by future outcome (very large
  remaining tail; H100 movers; +50-not-100; +30-not-50; ordinary; clear failures).
- Outputs: conditional surfaces `P(future +30/+50/+100 | state)`, `E[remaining MFE | state]`,
  `P(new high before meaningful adverse move | state)`, and the same conditioned on remaining
  (forward) value — an H100 name that already made +95% is different from one with +150%
  still ahead.
- Gates: deliberately simple deterministic "golden-window" gates, **≤3 conjunctive
  conditions**, each from the feature vocabulary; each gate named, dual-block checked
  (§5). Full conditional tables are reported whether or not a gate is built.

### 3.3 F3 — Survival / weakening / release research
- Structures to map (own path only): entry-relative deterioration (depth, duration,
  recovery speed); peak-relative deterioration (drawdown from running high, % of MFE
  surrendered, time since high, failed reclaim); magnitude × duration × recovery interplay.
- Release modules (finite): `R2(L,w)` L ∈ {10,15}, w ∈ {3,5,10} (breach → grace window →
  release iff still below at window end, else disarm); `R3(g)` g ∈ {40,50,60} (close below
  (1−g)·running peak); composites = any-of/conjunction over R1/R2/R3. No thresholds outside
  these grids.
- **Do not grid hundreds of thresholds**: map the structural relationships first, then
  instantiate modules.

### 3.4 F4 — Scale-out (partial exit engine)
- Sizes: 25%, 33%, 50% of current shares per action (100% = EXIT handled by release).
- Staged patterns: {25→25→rest}, {33→33→rest}, {50→rest}.
- Triggers: the §3.3 module vocabulary, or a golden-window deterioration gate (§3.2).
- Per-ticket mode (only the deteriorating member) and basket-level de-risking mode
  (declared: when ≥K of N members simultaneously satisfy a deterioration module, reduce all
  by x%; K ∈ {2,3}, x ∈ {25%, 50%}). Basket-level de-risking is measured, not assumed to
  help. Report the EV contribution of each tranche.

### 3.5 F5 — Scale-in (strengthening engine)
- ADD sizes: +25%, +50%, +100% of the **original unit notional**; staged adds permitted;
  total adds ≤ +100% (cap 2× unit); unfunded adds are skipped (cash constraint, sim
  contract). Adds are justified by the candidate's **own** state improvement only.
- Triggers: new-high continuation; recovery-after-breach (reclaim inside a declared grace
  window); golden-window strength gate (§3.2). One-add and staged architectures.
- Mandatory output: does added capital earn its incremental EV, and how much remaining tail
  exists at the add moment (09:45/10:00 adds).

### 3.6 F6/F7 — Staged capital and released-capital redeployment
- Reserve fraction p ∈ {1.0, 0.67, 0.5, 0.33} of C0 deployed at entry; the remainder is
  reserve. Reserve deployed at one golden checkpoint (585 or 600) by rule ∈ {cash,
  equal across surviving members, golden-gate survivors}. p=1.0 = no reserve.
- Released capital (on any exit) policy ∈ {cash, equal among surviving members, golden-gate
  survivors}, with recycling fraction ∈ {0, 50%, 100%} of released notional.
- No leverage anywhere; total gross ≤ C0. Never pour everything into rank #1 by assumption.

### 3.7 F8 — Joint basket economics (multi-survivor lens)
- Quantify the full joint economics per entry family: P(all positions profitable at EOD),
  P(all reach +5/+10), P(≥2 profitable), P(≥2 reach +20/+30), joint MFE and EOD
  distributions, pairwise correlations, multi-meaningful-survivor day frequencies,
  conditional joint behavior by golden-window state where available. The one-giant-winner
  day shape is one shape among several; release logic must respect what the data shows.

### 3.8 F9 — Weighting / sizing (selection alpha vs sizing alpha)
- Schemes (fixed total gross): equal (baseline); rank-linear (weight ∝ N−rank+1); mild
  initial score-gap tilt; golden-gate multiplier (1.5×) for gate-passing members;
  survival-state weighting (same multiplier applied at a later checkpoint). No continuous
  optimizer, no per-name fitting. If complicated sizing cannot beat equal robustly across
  blocks, discard it.

### 3.9 F10/F14 — Weak-regime and environment read
- Causal regime labels knowable pre-entry or intraday-morning: broad-market trend proxy,
  small-cap trend proxy, realized-volatility regime, breadth/extreme-mover frequency,
  prior-session trend. ≤2 thresholds per label, fixed.
- Report strategy economics **inside weak regimes** and by year/quarter; environment
  variables: PM leader strength, top-K dispersion, count of names over meaningful PM gains,
  aggregate dollar volume. Relationship maps first; any gate is a declared module with
  dual-block evidence; no trade/no-trade classifier zoo.

### 3.10 F11 — Execution realism and capacity
- Compare stored-open convention vs conservative minute execution vs quote-aware execution
  where high-quality quotes exist; friction ladder {0, 50, 100, 150, 200} bps; measure
  opening spread, slippage proxies, blocked/late fills, halts, dollar volume, size
  feasibility; produce capacity curves (approximate deployable notional before execution
  assumptions materially change) using the net-size artifact. Account size is NOT decided
  here; size follows evidence.

### 3.11 F13 — Leaderboard truth (production data source)
- Verify end-to-end that canonical selection is self-computed from full PIT-universe SIP
  data; investigate Alpaca screener availability/alignment and discrepancy mechanisms;
  document what a production bot needs to compute the identical ranking live. No strategy
  semantics change.

## 4. Degrees-of-freedom register

**Counts as a new degree of freedom** (needs declaration + fresh unseen months for any
claim, and invalidates like-for-like comparability until re-run):
- any entry anchor/T outside §3.1; any N outside {2,3,4};
- any rule form or threshold outside the §3.3–§3.5 grids; any gate feature outside §3.2;
- any capital scheme outside §3.6; any sizing scheme outside §3.8; any regime gate beyond a
  declared ≤2-threshold label;
- any change to `BASKET-SIM-CONTRACT.md`; any outcome-dependent narrowing of the grids.

**Rules**: every tested cell is reported (full surface); dev-fitted selections (any "best
cell") cannot be called confirmed — confirmation requires unseen months at a later gate.
Zero-fixation doctrine stands: no +30/+50 targets, no "one survivor" assumption, no
champion prediction; rulers stay rulers.

## 5. Metrics, stability, and reporting contract

- Every run emits the standard metric table (sim contract §9): mean/median basket-day,
  daily percentiles, worst day/week/month, positive-day share, deployed capital, turnover,
  entry/add/reduce/exit counts, failed-ticket cost, survivor contribution, top-1/5/10 day
  profit shares, path-class contributions (+50/+100/+200), false-release rates, tail
  retained vs raw MFE, compounded growth + drawdown (secondary), gross/net@100/net@150,
  month/quarter/year surfaces, parameter neighborhood.
- **Dual-block reporting (mandatory)**: block-1 = 2021-02…2023-12 (35 months), block-2 =
  2025-02…2026-05 (16 months). Effects confined to one block are flagged **not stable**.
- Inference: month-resample bootstrap (10,000 draws, seed 20260922) for CIs; month-blocked
  surfaces everywhere. No random splits of correlated observations (time-blocked validation
  for any fitted model; minute observations from one day never split across train/validate).
- Evidence labels: ART / RUN / REPORT / UNVERIFIED / WRONG. Anything quoted in a summary
  must carry its label and artifact path.
- Each family README reports: mechanism (economic idea), incremental EV vs primitive
  baseline, cost (tail, drawdown, turnover, execution), stability (blocks, neighborhood),
  interaction (complements/duplicates other modules), implementation value (should it enter
  the combined architectures).

## 6. Sequencing to the next gate (Phase 3)

1. Phase 2 produces the **strategy landscape** (module-level implementation knowledge) for
   owner review. No final strategy selection inside Phase 2.
2. The orchestrator then builds ≤3 **combined architectures** from modules that
   independently earned their place. Each combined architecture is declared on paper
   (module list + fixed parameters) **before** its outcomes are inspected, evaluated against
   the strongest primitive baseline, and reported with the full standard table.
3. After the landscape report and owner review, Phase 3 freezes ONE simplest faithful
   implementation (signal + execution) and only then performs the one-shot holdout
   inspection (sealed 2024/2025-01 first; reserved 2026-06/07/08 much later, per PRE-REG-01
   §7 as amended by the owner on 2026-09-22). No holdout is touched during Phase 2.
4. User sign-off at each gate.

## 7. Explicitly NOT in this document

No selected parameters (entry time, N, L, w, g, sizes, reserve fractions); no final
strategy; no Phase-3 strategy P&L; no holdout access; no live bot changes; no H025 mixing;
no overnight strategy; no leverage; no dynamic replacement beyond declared recycling; no
black-box controller; no resurrection of retired formulations.

## 8. Change log

- 2026-09-17/18: legacy draft (single-rule contract on the legacy tape; freeze paused at the
  SIP upgrade). Superseded.
- 2026-09-22: rewritten, on owner directive, as the family-level Phase-2 implementation
  contract and FROZEN before any Phase-2 strategy outcome was inspected. Legacy constants
  (T\*=600, L=10, g=50, N=3) demoted to owner decisions, non-binding. Phase-1 measurement
  layer remains frozen; sealed/reserved boundaries unchanged. The PRE-REG-BASKET-01 §7 "ONE
  release rule" gate is amended: the single-implementation freeze moves to Phase 3; Phase 2
  freezes the family space (this document) instead.
