# STATE — current truth (latest update 2026-10-03). Full chronicle: factory/STATE.md (append-only log).
# Hypothesis ranking + falsifiers: researches/HYPOTHESES.md (living).
# History before 2026-09-04: researches/CANONICAL_STATE.md (superseded snapshot, kept for trust map).

## UPDATE 2026-10-03 — owned-claim lifecycle dollars (corrected two-method replays + three exploratory diagnostics COMPLETE; declared source_correction_status NOT_VERIFIED — parent source/runtime checks observed PASS)

**HISTORICAL FIRST PASS (kept, INVALID-AS-CAUSAL — do not read as evidence).**
First chronological action-value pass completed on383 discovery-test days:
all176 portfolio cases negative at100/150bps, N3/N5 reported separately.
Best N3/09:29 tape stopping: gross+0.477% less modeled fees0.985% = net-0.508%.
State-dependent stopping is substantially less bad than fade/hold, but not an edge.
N5 does not repair the deficit; history/tape/reinvestment do not reliably pay.
Full533-day event corpus (673,035 events), joint dollar surfaces and exact
loss/fee/rank/concentration attribution are committed as owned_claim_* artifacts.
Entry is sunk in owned-share decisions; fresh re-entry has a separate cost hurdle.
Current eight-event value iteration dumps09:00 claims around09:10, before many
claims mature; full learned-policy-return labels are the next controlled test.
The topology/funding findings above are instrument findings, not roster falsification.
These first-pass numbers (FVI8 and first `policy_return`) are now known defective:
future-status peer/scanner leakage, a strict `next_sequence` pointer that skipped
same-open chronological releases, excessive unused-suffix censoring of the fit mask,
and the `value_iteration` strict-control entry gate misapplied to `policy_return`.

**CORRECTED RESULTS (later same day — completed corrected runs; first-pass numbers above remain
HISTORICAL / INVALID-AS-CAUSAL).** Corrected full-ruler rebuild completed on the discovery half
only: 533 days 2021-02-01..2023-03-14, 673,035 causal events, 160 tape-view feature columns
(state90 / history134 / tape160), 9,895 claims, 638,198 valid rows, 24,942 unknown rows, 0
beyond-session; three expanding chronological folds (150→150→300→450→533) = 383 test days. Two
methods completed: `value_iteration` (FVI control; `owned_claim_policy_discovery.json`) and
`policy_return` (full learned-policy cashflows; `owned_claim_policy_full_discovery.json`), each
176 cells at 100/150bps (352 cells combined): **0/176 net-positive in EACH method — no
positive-EV executable cell.** Control least-negative cell: 571/N3/50bps `state:stop` net
−0.5140% (gross +0.4475%, modeled fees 0.9615%; 380/383 known days); best N5 control 571
`state:stop` −0.6631%. Full `policy_return` is materially worse: N3 least-negative 540 `fade`
−2.4535%; best N3 stop cell 540 `tape:stop` net −3.6058%; best N5 stop cell 560 `tape:stop` net
−2.9706%. Cash/fee reconciliation over 704 books: max |Σ member net − Σ daily ret| =
4.529709940470639e-14 (common-day subset 4.263256414560601e-14). Eight financial regressions
passed (parent-exercised) in `factory/scripts/test_owned_claim_causality.py`. Because no
corrected cell is positive, the instrument remains a failed harvesting instrument — NOT a roster
falsification. Declared machine `source_correction_status: NOT_VERIFIED`
(`owned_claim_findings.json` actual value) — no structured source-correction verification record
was published into the reader inputs. SEPARATELY OBSERVED (do not conflate with the declared
status): parent source/runtime checks passed — source pins observed (push script
`provenance.script_sha256` matches; top `source_sha256` composite), 8 financial regressions passed,
EconomicAssumptionReview review PASS. Still NOT a certified edge: no alpha validated, no quote
certification, freeze, deployment or protected-half read. [Earlier revision of this section labelled
it `VERIFIED / parent provenance recorded`; that was an overclaim and is withdrawn — the machine
status remains NOT_VERIFIED and the passed checks are observations, not a published verification
record.]

**THREE EXPLORATORY DIAGNOSTICS (EXP-85, same experiment; bg_523 full run 705.56s, source pins
verified).** Discovery-only; UNITS DIFFER — none is a validated policy or edge:
- `push_legs` (`owned_claim_push_legs.json`): NOT a portfolio-P&L surface — causal first-push
  dollar anatomy. 533 dates / 9,895 claims / 5,575 first pushes (2,791 clean, 2,784 damaged) /
  63,424 declared-leg events. Pooled-clock N3 clean hold increment $/100: 15m +0.4887
  [CI −0.3885, +1.3673], 30m −0.5939, 60m −2.3122, 120m −3.8442 (distinct from the clock-540-only
  surface).
- `probe_reserve` (`owned_claim_probe_reserve.json`): IS a modeled portfolio cash/wealth endpoint
  diagnostic (units = modeled portfolio cash/wealth, unlike the other two). 48 frontier cells —
  early endpoint 0 positive; late endpoint only 2 positive, both 540/N3/100bps: 15m +0.019334%
  portfolio (SE 0.120457%), 30m +0.102710% (SE 0.200719%); break-even α 0.008657 / 0.044336. All
  150bps cells and all N5 late cells non-positive. Still a diagnostic, not a validated edge.
- `retrieval` (`owned_claim_retrieval.json`): NOT a portfolio-P&L surface — held-out
  retrieval-scalar evidence. evidence true; 3 fixed folds; 32,514 anchors and 75,076 queries;
  pooled-focus expected-positive realised $/100 negative at each horizon/N/mode; scalar TRAIN
  baseline lower/equal RMSE in 77/80 cells (all 16 focus cells); no classifier (claim boundary:
  held-out scalar means are not a fill model).
- Paired parent calc (EXACT same clock/N/cost/policy/date, so no cross-clock causal comparison):
  540/N3 `tape:stop`, 367 dates — control −1.2992% vs full −3.6058%, delta −2.3066pp; 571/N3
  `state:stop`, 359 dates — −0.4241% vs −3.7461%, delta −3.3220pp.
- Producer lineage note: `owned_claim_attribution.json` producer_sha `bd6e1741` EXACT-matches the
  archived git blob `76db4a1:factory/scripts/owned_claim_attribution.py`, NOT the current formatted
  script `8942b8e7` — treat it as ARCHIVED lineage, not current-producer output (no rerun needed,
  format only). Other current pins (events / helper / model / replay / surfaces / loss / probe /
  retrieval) verified.

**LOSS-READER RERUN DONE — earlier quarantine superseded (asserted).** Parent fixed two reader
aggregation bugs (concentration merged N3/N5; `release_before` joined both N views inside each N
loop → duplicate N3 keys / incorrect denominators). The final formatted full rerun bg_531
completed in 320.57s; its source pin and the 2,112 unique release keys were asserted in Eval, and
`concentration` reports 352 N-separated cells matching the decomposition (release bounds
re-asserted in the same cell on the next pass); max cash residual 4.529709940470639e-14. The
earlier "do not publish" quarantine is superseded; the historical warning is retained. Core policy
results and cash reconciliation were never affected.

**CAUSAL STATUS CORRECTION (same day, appended).** The FVI8 (eight-event fitted value
iteration) and first `policy_return` numbers above are **PROVISIONAL and NOT a valid causal
EV assessment**: (1) future-status peer/scanner leakage in the inherited predictors;
(2) the strict `next_sequence` liquidation pointer skipped same-open chronological releases;
(3) excessive unused-suffix censoring (outcome status frozen into the fit mask);
(4) the `value_iteration` strict-control entry gate wrongly applied to `policy_return`.
Corrections are landed in `owned_claim_*.py` source with **8 passing regression proofs**
(`factory/scripts/test_owned_claim_causality.py`); the full corrected 533-event rebuild and
the 383 test-day two-method (FVI8 + `policy_return`) replays are now **COMPLETE** — see
CORRECTED RESULTS above: both artifacts exist, reconcile to 4.53e-14, and report 0/176
net-positive cells in each method. Old numbers stay
as historical INVALID-AS-CAUSAL observations, not erased, and are not a roster falsification.
N3/N5 are independent economic views; original entry is sunk while fresh re-entry carries its
own cost hurdle; causal quantities are fixed original-share/fixed-buy and price-gap funding is
UNKNOWN; no protected-half read, freeze or deployment. First+5% push moves and the dollar-window
anatomy remain descriptively useful. The exploratory push-legs / recovery-retrieval /
probe-reserve scripts (EXP-85) are now COMPLETE (bg_523, 705.56s): three canaries passed and the
full outcomes are reported above as discovery anatomy/diagnostics only — no policy, classifier or
promoted edge; units differ per diagnostic. Bot: NOT closed — pending restart-to-existing OCO
integration and final gates (obsolete T5 failure counts are not the latest boundary). No live
code/flags touched (H025 constants/signals/sizing preserved).

## UPDATE 2026-10-03 — research takeover: harvesting, not selection substitution

Active research checkout: `basket-phase2-f1` (main ends at the September-22 freeze).
Reanchor Stage F/G's “pop already complete / entry or selection is the problem”
interpretations are withdrawn as unearned; their negative cashflows remain evidence
about those implementations. Same-roster executable-open opportunities remain large.
New discovery-only producer/readout: `lifecycle_harvestability.py` /
`lifecycle_harvestability_read.py`; committed evidence
`factory/artifacts/lifecycle_harvestability_discovery.json`, detailed day artifacts
`data/harvest01/lifecycle/v2/harvestability/`. 533 days, 528 correlated reported cells,
703,560 member cashflows and 27,608 first-push event records; every cell negative
at 100/150bps. Before 13:00, +5% close signals occur on 48–51% of selected slots,
next-open sale proxies average +6.9–7.5% gross; 09:00 fade sells 157/310 later
+30%-signal claims too early (50.6%, near-open 18.5–21.8%). Static banking shrinks
the tail as well as tax. Next: learn trajectory-conditioned repair/terminal-decay
and monetization/retention decisions, not another monster classifier.
Shared replay's 5%-original-capital deadband was reproduced; new diagnostic uses
exact shares. Route “captured” and “second leg” labels need correction before reuse;
100bps remains modeled, not quote-certified. No freeze, no deployment, no new
second-half outcome read; H025/bot and unrelated work unchanged.

Late economic-audit qualifications: `lifecycle_preramp.py:109-113` includes
pre-entry bars in MFE/MAE; its “forward +22–28%” is quarantined. Descriptor monster
counts also require >=50% terminal return; personality uses a different definition.
Near-open +120m ranking reports contain +0.53/+0.60% net candidate-return averages
over 233 days, **not portfolio P&L**; the historical blanket-negative claim is too broad.
Prior HARVEST01 already viewed the wider 1,066-day calendar. Lifecycle scoring is
held out, but it is not globally pristine economic evidence. No new half-read occurred.

## UPDATE 2026-09-30 (night) — canary GREEN at pin f9346e6b
Canary closed: B3≡A3 (58bbbf0c) and C≡D (f9346e6b, +UTC-timestamp row-group pruning) all 50/50 with
125/125 payloads byte-identical across generations; verify-canary exit 0 (43/43, core_hash
ffd513ce…). ~2x faster, RSS 5.07→3.2 GiB. Coverage defects closed (NTZ observed; 54 B1/173 B2
off-hours-only → 0 unresolved). Full 1,066-day corpus launched after this commit.

**CV01 (authorized 2026-10-01 late, after EV-01; A/B DONE, audit PASS).** Two dev-only diagnostics runs A/B (not TestC, not a model), parent contract `factory/artifacts/basket/phase2/ATLAS/CV01/contract.json`; primitive **CV = W_hold/W_exit − 1** on the current next-open liquidation-$ baseline (entry cost sunk, sell fees common/cancelling); arm B = first giveback-10 sell → completed close ≥ GROSS actual exit price → STRICTLY later next-open buy; one cycle, no outsiders/sizing/reserved months/sub-minute/models; states past-only, incomplete partial horizons UNKNOWN (never 0), giant attribution never state. Readout: `factory/artifacts/basket/phase2/ATLAS/CV01/report.md`.
**Results (dev anatomy, not policy; h = ET minutes):** A balanced CV h60 −0.438% / h120 −0.560% / h240 −0.805% (occupancy −0.449 / −0.708 / −1.138%), h0 exact 0; B: retaining beats this re-entry rule over the reported horizons h1–h240 (B−A −0.74/−0.81/−0.73/−0.66pp at h1/30/60/120) and **both branches fall below cash from ~15 minutes on**; no claim beyond the reported horizons.
**EV01 is RETROSPECTIVE ANATOMY, not causal alpha** — it reads 30/60/120 objects later than the +1/+5/+30 decisions plus the whole future-N universe, so its earlier PASS is statistical separation, not actionability. Owners: SharedValuationBuilder / ContinuationAnatomy / OwnershipContinuity / CurrentDollarAudit. No profitable claim; a failing management family does NOT kill Atlas or race admission.

## UPDATE 2026-09-30 — Tape Atlas (ATA) observation lane: repairs solid, canary NOT complete (SUPERSEDED by the night update above)
Read together with `factory/STATE.md` "2026-09-30 (later)" (authoritative chronicle paragraph).
- **Current lane**: `researches/PLAN-TAPE-ATLAS.md` (observation before geometry, geometry before
  future anatomy, economics only afterward). Prior E1/E3 negatives constrain their tested
  formulations, not raw sequence/retrieval/race discovery. The 2026-09-27 bullet's "**E1a (open)**"
  line is **superseded**: ATLAS Phase 2 closed 2026-09-28 (E1 has no dollar increment over the
  trivial giveback:10 ruler; E3 null) — see `researches/ATLAS-PHASE2-CLOSURE.md`.
- **Solid, committed data repairs**: `8f2ff07` net manifest index rebuilt 3,198 → 3,204 entries
  (sha256 `446090fd…`), repair+verify exit 0, no source mutated; `dcefbd5` joint acquisition
  admission B1 19/19 Feb-2025 (`v4`) + B2 41/41 Apr/May-2026 (`v3`), zero missing/stale scopes, zero
  residual same-feed gaps, all origin snapshots SHA-bound. Blockers B1/B2/B5/B6 bind to actual
  evidence **files** in `OBSERVATION/v0/blockers_resolved.json`.
- **NOT done — treat as unverified**: the 20-day observation canary never completed (three kernel
  global OOM interruptions at 11:01/12:02/13:25 on 30-Sep; see
  `local://wsl-interruption-investigation.md`) and **no `verify.json` exists**; the tracked
  `canary/summary.json` is the pre-repair artifact (old blocker id
  `B6_missing_net_manifest_2026_05_21_29`, `full_v0_ready: false`,
  `determinism_vs_previous_manifest: null`); recovered bookkeeping is 117/120 layers with 2025-03-03
  not re-hashing, so a `--force` rebuild (not a resume) is required. Run 1 built all 20 days then
  exited 1: `day_registry` built its 1,066 day dicts under polars' default 100-row schema inference,
  so acquisition string fields inferred Null for the first ~1,006 dev days and the first acquisition
  day raised `ComputeError`; now fixed with full-length inference (the registry builds and 60
  acquisition days resolve). No OOM/signal (peak RSS 4.71 GiB, reported under the pre-pin ÷10⁶
  conversion — see the Ops note), no new manifest/summary/costs/selftest written, and the source
  changed mid-run, so run 1 is smoke-only and
  the tree's `canary/summary.json` remains the stale pre-repair artefact. The producer is re-frozen at
  sha256 `32a1d082…` and its owner is on hold for the whole A/B/verify chain. **The contract lock is not
  valid right now** — `schema.json` is stale against it (one `--stage contract --force` re-freeze is
  authorized; the edit adds exactly two `day_registry` coverage fields and changes no market payload,
  so runs A/B must be identical everywhere). Then hold for the new SHA, a fresh A/B run,
  `verify-canary` and audit. No full 1,066-day corpus, no outcome-blind inspection, **Freeze R not frozen**
  (`representation_matrix_template.json` `DRAFT-NOT-RUN`), no nearest-100 proof, no SSL run, no race
  tiers. The within-name geometry producer and masked-TCN sequence family are prepared/scoped-only —
  no empirical finding. **No discovery, retrieval or alpha claim exists in this lane.**
- **Identity/microstructure facts retained (non-blocking)**: DXR 2025-02-03 = 24 SIP trades /
  501 shares and **no** SIP minute bar vs one unreproduced HF/Finnhub 12:55 bar (7.78, 124 shares) —
  a missing bar is not a missing trade; four cross-feed witnesses (BYNO, DXR, FTII, VISL) preserved
  as original proxy rows; three fixed-width padded PIT names (`ECC`, `ETX`, `SAND`) corrected
  injectively (5,535 → 5,535 distinct, collision-guarded), which makes the Feb-3 accounting close at
  0 unresolved.
- **One heavy owner**: `CanaryRecoveryOps` alone runs canary/full; all other work is light
  code/doc/metadata. No signals, no process kills, no WSL restarts (cooperative stop only). The three
  30-Sep interruptions were kernel global OOM kills of `python` (11:01/12:02/13:25), not agent
  actions; the child cgroup cap is unproven and not adopted. Host is now 16 GiB + 32 GiB swap. Ops
  holds the `>= 10 GiB` cold start before the parent is loaded; the script's own gate is one shared
  calculation (`cap = floor(min(8.0 − current parent VmRSS, MemAvailable − 2.0 GiB) / 5.0 GiB)`, cap 0
  refuses; the 6.0 GiB figure is contract text only). Peak RSS now uses exact ÷2²⁰ GiB (was ÷10⁶, a
  +4.86% over-report), so peak figures must not be differenced across that pin. The `RTH_LO` question
  is **CLOSED**: the board stays RTH-trimmed with no clock moved; coverage now derives from the
  untrimmed declared `[565, 965]` window with two published populations (`raw_n_pit_symbols_full_window`,
  `raw_n_pit_offhours_only`), after the real NTZ counterexample (2025-02-03, one bar at 16:01, 137
  shares, counted as missing). Commands: PLAN §10.1.

## UPDATE 2026-09-27 — BASKET Phase 1 (ATLAS measurement) CLOSED; Phase 2 = E1 execution test
Canonical entry: `researches/PLAN-ATLAS-01.md`; numbers: `factory/artifacts/basket/phase2/ATLAS/`;
readable conclusion: `researches/ATLAS-PHASE1-SYNTHESIS.md`.
- **Panel v2** (SIP, 1,066 dev days, 2017/2021-2026 + 2022-04 stub; 25,788 runner-days; 1,900,432
  state rows; 100% point-in-time SI coverage 2017+, IDs zero) is the measurement backbone; block
  boundaries frozen (`block_of`), reserved 2026-06..08 untouched, 2024/2025-01 sealed.
- **Ledger v2** = same-bar forward VIOLATION; primary = next-bar PATH_RETURN on the canonical 25,788
  complete-path runner-days (arms: hold, giveback:10, peak_pct:0.03/0.05/0.10, timestop); window v2
  adds the 09:30-11:30 morning sleeve + dedup; every table dual-block, top-5-day-removed, day-clustered.
- **Fall v2** (final sha 19e98a19...): state predicts *forward dispersion* strongly (within-clock AUC
  0.67-0.74 across families/blocks); the exhaustion barrier is a real but weaker,
  dispersion-independent score-level signal (like-for-like +0.038/+0.044/+0.037/+0.009 over hazard;
  within-decile 0.675-0.702); per-decile coefficient allocation is UNIDENTIFIED and withdrawn as
  mediation; no directional separator in the 1-minute state (the honest two-sided test is adverse).
- **E1a (open)**: cross-fitted P(this new high is the last) -> release when the score is high; first
  execution test, held/censored excluded; killed if flat vs the frozen rulers in either fold.
- **Ruler honesty**: the in-sample price-perfect table is UNATTAINABLE (even q10 rows have higher
  prices ahead); the in-sample estate table is oracle contamination, never a valid bar.

## Where we are (2026-09-13)
Phase: LIVE PAPER VALIDATION of the first OOS-passed mechanism (stack STOPPED per
factory/STATE 2026-09-16c; restart = rm data/KILL); research lanes closed except the new
BASKET-01 planning lane (bullet below).
- **H025 FLUSH RULE = OOS-PASS-PAPER.** Frozen `PRE-REG-FLUSH-01.md` passed untouched OOS
  2024-01..2025-02 (pf>=2 n=381, +1.14%/trade net 100bps, 12/14 months). `flush_bot.py`
  v2.1 is armed `live:true` on a dedicated Alpaca paper account (flat, 0 fills as of
  2026-09-13). Mechanism: extreme top-3 leader in fresh+thrust state gets a resting -10%
  bid; fast flush fills at B; exit toward c0 (OCO target at c0, 0.9B stop, tl30).
- **Judge live fills against the A3b baseline, NOT the frozen one** (HANDOFF §14):
  pf2 +1.13%, 10/14 months, ~19 fills/mo post-overlay, worst -3.1%; fragility: 55% win,
  6.3pp cushion, bootstrap CI touches 0, ~14 months of forward fills needed to exclude zero.
- **Closed (tested negatives):** post-fill management incl. flat/conditional time-stops
  (`PRE-REG-EXIT-01`, H028 RETIRED) - the edge IS the resting limit at c0; IEX-only feed
  replay (`PRE-REG-MICRO-01` Study A) - failure was a fill-venue artifact, the state feed
  survived via A3b; unsupervised pattern digests (all-minutes v1/v2 and event-anchored
  E1/E3; `PRE-REG-PATTERN-01/02`, H030 RETIRED) - no separable shape families at
  1-min/60-min on the top-3 population.
- **H029 day-breadth (user hypothesis) DOWNGRADED** (H029r): quiet days (0-1 strict names)
  beat busy days OOS (+1.20% vs +0.46% all fills; rank1 -0.69% vs +1.33%) but the
  day-clustered bootstrap CI includes zero and permutation p=0.19 - OOS-only, not robust,
  NO gating. Live `n_strict_est` + `pf_est` tags flow into the ledger for a free forward test.
- **Next:** forward paper accumulation (~30 fills) -> judge vs A3b -> tiny real-money
  sizing decision. Remaining ML branch = learned sequence embedding (own pre-reg) or more
  data/power (2021+ backbone staging, real-time SIP); both are priced decisions. SIP
  ~$99/mo is NOT needed for the frozen strategy (IEX covers a median 24.6% of session
  minutes per top-3 name; only sub-minute features would justify it).
- **BASKET-01 participation/survival thesis (H038; Phase 1 measurement complete 2026-09-21).**
  Phase 0 docs frozen: `researches/THESIS-BASKET-01.md` + `researches/PRE-REG-BASKET-01.md`.
  Phase 1 regenerated on the SIP substrate (1,066 dev days, QA PASS) with the repaired read
  layer; canonical packet `factory/artifacts/basket/sip/READ_PACKET.md`. Advisor-directed
  final measurement pass done 2026-09-21 (B(T) prev-close admission gate removed; A_pm31
  09:31 bound; trade-level T5; T7 monthly rollups + T8 fix; EOD/prev leader objects;
  capture funnel; race_by_view; provider-fetch recovery -> unresolved 9 -> 2). Audit pass
  2026-09-21 (five scoped audits + two independent verifiers): ranking tie-break made
  explicit -> selection audit 15,990/15,990 (100%); T8 N=3 + pays_days fixes; T5 fill-as-
  state-zero and raw-chronology race; floored leader objects; all headline numbers
  reproduced from lower-level inputs. Integrated
  seven-question read pending owner gate. No release-rule constants; PRE-REG-BASKET-02
  unfrozen; H025 untouched. Reserved months 2026-06..08 untouched; sealed 2024/2025-01
  acquired mechanically (certified) and unseen.
  **UPDATE 2026-09-24 (C1 cycle): this bullet is stale.** PRE-REG-BASKET-02 is frozen
  (2026-09-22); Phase 2 ran and was corrected (C1). Corrected F1 (120 cells), F3 (20) and
  F4 (54 partials) are ALL net-negative on 1,066 dev days; F5 exists only as a 48-cell
  diagnostic (0/32 add cells positive in both blocks); F6 is a bounded p=.50 diagnostic;
  F7 is unrun; no F13 parity artifact, no Phase-3 freeze. Diagnostics this cycle:
  post-touch mixture 60.0%/40.0% continue/fade (pooled), coarse minute state does not
  separate them (best AUC ~0.57), the +30% harvest gain is mostly touch identity rather
  than time-in-market (time-matched identity-shuffled placebo), and the simple
  resting-limit entry refutation stands. Interpretation reset, ranked next moves and the
  drift list: `researches/SWARM-SYNTHESIS-20260924.md`.
- The older sections below (phenomenology, collisions, 2026-09-08 strategy) remain valid
  history; the 2026-09-08 "current strategy" block is superseded by the flush-rule path.

## What is solidly established (multiple provenances, harness-verified)
- Phenomenon (canonical, reconciled 2026-09-16; factory/artifacts/runner_phenom_reconcile.json):
  141 genuine >=60% open->close runners over 172 days (2025-05..12 + 2026-03) = 0.82/day;
  90/172 days (52%) contain >=1; every month represented (monthly 0.57-1.15/day), no dead
  months. The source artifact also holds 82 fallback day-#1 rows (days with zero >=60%
  names; mean +45%) - NOT runners. The old "1.2-1.5/day" was the mixed rows/day (1.30)
  and is superseded. Frequency is regime-stable across the 9 scanned months.
- Runner shape (genuine 141 only - uncontaminated): median open->high 322min; median 50%
  of the move by 11:25; only 15% half-done by 10:00; afternoon contributes median 55%.
  Halts fingerprint the process (84% of runners halt; dose-response 0 halt -> +83% mean
  gain, 11+ -> +177%; retrace >30% mid-move didn't kill 24/141, mean +244% finish).
- Thrust alone is DEAD (+0.3% remainder, n=5,521). Rank/health/E1/E2/E3/Ridge/DTW/
  snapshot selection: all failed conditionality at scale. Retired, thesis-level.
- Buying halt reopens is DEAD causally (next-bar-open D30 -0.5% to -1.5%). The
  halt-gap money (morning qualifying events: mean +6.3%, median +4.4%, n=68) accrues
  only to the already-long.
- MFE-before-drawdown is the most stable measured property of top-gainer cohorts
  (mb100 positive in 9/9 month blocks, 31/33 days) — an opportunity map, NOT an
  entry signal (H11 was its capture attempt and failed).
- Money-location map (measured on the GATE cohort — 15-30% by 10:30 + >=1 halt,
  n=180 dev name-days; not general-population): morning halt-gaps (if holding),
  10:30-11:00 inflow wave (+1.2%), afternoon 14:00-16:00 wave (+2.0%). Midday is
  dead capital. The +2.39% afternoon rental did NOT survive collision months
  (2025-04 -6.27%, 2026-01 -3.51% net; pooled -2.13% at 100bps).

## Collisions: 0-for-4 (the meta-finding that shapes everything)
E1xhealth (146d) -> failed. Ridge (one formulation) -> failed twice (Aug, Oct). DTW
medoid October -> failed. H11 afternoon rental -> failed (2026-09-08). Pattern:
month-blocked dev positives with small per-month n are found easily and do not
replicate. SCOPE: this covers small-n selection/timing rules; the ML fresh-window
failure had high n (5/5 months negative) — that implicates regime/decay. Power is
necessary, not sufficient. Consequence: no selection or timing rule gets believed
again without (a) pooled n across MANY months before testing, or (b)
forward-observer accumulation as primary evidence.
Full retire list with causes: H8 gate (descriptive only; capture failed via H11);
H9 reopen participation (anchor illusion; causal entry negative); H10a hold-through-
halt (UNTESTED — open family, not promising); H10b/H11 afternoon rental (collision-
failed); plus historical: E1/E2/E3, gain-rank, health, Ridge, DTW, 10:00 snapshots.

## Current strategy for the next phase (decision 2026-09-08)
1. STOP inventing single-month-sliced selection rules. Statistical power first:
   any new test must pool >= 6 months of name-days in dev AND pre-register >= 2
   unseen collision months.
2. Forward observer = the honest evidence engine (P2 pre-reg frozen: researches/PRE-REG-P2.md — read before any measurement work). It runs daily, order-free, and
   accumulates live n (no backtest overfit). Priority: keep it running, score every
   session through score_forward_day.py, build the live record.
   RUNBOOK: `python factory/scripts/forward_observe.py --live` during ET market hours
   (logs to data/forward/YYYY-MM-DD/; starts idling until session open). After close:
   `python factory/scripts/score_forward_day.py <YYYY-MM-DD>` writes scores.json.
   Skip weekends/holidays (09-05/06 rows are weekend noise). It is NOT currently
   scheduled — a session must launch it manually each trading day.
3. Phenomenology remains the idea mine (money-location map above), but extraction
   formulations must be power-aware from inception.
4. Open (untested, not promising): hold-through-halt capture; turnover/float clock
  (needs PIT float data we lack); failed-move fade (conditions on late info);
  transitions/handoffs (needs event logging wiring).

## Open questions (unresolved; do not cite either side as settled)
- Rank-1 chasing conflict: Cameron-lane "causal minute-rank-1 negative every month
  both years" vs verify_core 11-point monthly h60 DOWNGRADED to mixed (-579..+411).
  Different horizons/universes; never re-opened head-to-head.
- FP-penalized recall gate for Stage B (MFE-before-DD target adopted).
- Harness-path rerun of MFE magnitudes (lane-only provenance currently).
- RESOLVED 2026-09-08: clean 2026-04..08 moved into data/backfill/ (live read path); load_day verified OK on 2026-04 and 2026-08; stale data/clean_2026-03 duplicate deleted.
- H10a hold-through-halt: the one open capture family (untested, power-caveated).

## Critical session-semantics (never re-learn these)
- et_minute() in replay_watchlist.py; session ET [570, 960); snapshot 10:00 uses
  et<=599, targets et>=600. PM data only via return_pm=True (separate frame).
- Month->path: >=2026-03 loads data/backfill/, else data/. 2026-04 clean file absent
  locally. 2026 premarket coverage absent; repaired premarket = 2025 only.
- MFE/MAE conventions: outcome_MB() low-based, entry-bar-included, same-bar
  drawdown-first (conservative). Never mix with older looser MFE numbers.
- Halt-gap proxy = >=5min hole in RTH minute bars. Trustworthy only for fast names
  + long holes; flat names print ~1M spurious holes (measured, excluded).
- Friction: use 100bps round-trip minimum for these names; adversary case says
  50-150bps realistic. 20bps assumptions are void.

## Live evidence sources
- factory/artifacts/*.json — every number above has a committed artifact.
- data/forward/2026-09-04..06/ — live observer days (09-05/06 are WEEKENDS —
  exclude non-trading days in analysis; 09-04 has the 16% phantom/warrant issue).
- H11 collision artifact: factory/artifacts/h11_collision_results.json (n=112).

## Provenance doctrine (adopted 2026-09-08 after audit)
- Every artifact's producer script must be committed under factory/scripts/.
- Every cohort scan must report distinct days covered (the 281-vs-245 gate-cohort
  confusion was a day-coverage gap, not a population difference).
- Friction is annotated per claim; cross-friction comparisons are void (20bps ML-era
  numbers are not comparable to 100bps H-era numbers).

## File-map doctrine (from 2026-09-08 mapping round — complete order)
- researches/: INTENT/STATE/HYPOTHESES active; CANONICAL_STATE + 07 frozen history;
  history/ = June bot-era audits (00-06), zero research constraint.
- factory/scripts/: ACTIVE = replay_watchlist, forward_observe, score_forward_day,
  test_forward_observe + data-ops chain (download/audit/clean/certify/validate/
  run_fresh_window) + ML machinery (build_features, train_ml, live_admission,
  sequencing, exposure_design, eval_frozen); RETIRED-KEEP = all evidence producers
  (H-series, path program, runner/gate/halt/h11, stagea, verify_core, feed_fingerprint);
  DEPRECATED = rank_day, extract_events (lineage only).
- factory/artifacts/: 8 committed JSONs = EVIDENCE-KEEP; ml/ specs+models = LINEAGE;
  h006-010 summary/report = EVIDENCE, parquets regenerable (c10/c20 dup pairs pruned).
- data/: canonical = clean_ohlcv (2025 data/, 2026 backfill/), premarket_2025,
  forward/ = live evidence; raw ohlcv = rebuild source; _scratch/_superseded =
  scratch. CLEAN 2026-04..08 NOW IN BACKFILL (was orphaned; P2 unblocked).
- Bot docs: SOUL/SPEC current (header version stale); README stale (pre-runner era);
  bot = separate workstream, implements only validated research.

## P2 verdict (2026-09-08, pooled 13mo)
Map structure replicates at ~half magnitude; M4 level (+0.59% gross) is
all-tail (excl top-5 -> -0.09%) and cannot survive friction. H11 death confirmed
structural. P3 restricted to replicated components; own pre-reg required.

## Leaderboard contract (2026-09-09, binding — do not drift)
- TradingView-equivalent: US listed stocks (NASDAQ/NYSE/AMEX), stock/common,
  junk-ticker suffixes excluded; rank causally by gain vs IMMEDIATELY previous
  trading session close, desc.
- Causal price at decision time t = close of the last bar stamped et<=t-1
  (bar t-1 closes at t; no shift(1), no extra staleness).
- ARBITRARY decision times supplied by the research. The leaderboard function
  must NOT bake in any H12 grid, target, horizon, or evaluation structure.
- Live source: TV scanner API (factory/scripts/tv_leaderboard.py, cmd live).
  Historical: same script, cmd hist — prev-close crosses month files by
  IMMEDIATELY prior session date, not prior month's last session.


## Reading discipline and interpretation status (2026-09-24, C1 era)

Read `researches/INTENT.md` "Research philosophy" before interpreting anything below. Three
categories must stay separate:

* **Verified evidence (corrected C1 engine, canaries 7/7 PASS):** F1 primitive surface (120
  cells, all negative, means -4.70%..-2.04%); F3 release map (20 cells, all negative,
  R2(L10,w5) best at -2.84%/day with failed-ticket cost -11.7% vs R0 -3.94%/-15.7%); F4
  scale-out (74 cells, all partial cells negative, partials +11..+101 bps/day vs hold and a wash
  against matched full exit, 2/54 positive in both blocks); F5 scale-in diagnostic (0/32 add
  cells positive in both blocks, most add signals unfunded in a fully deployed sleeve); F6
  decomposition (withholding alone leaves per-dollar EV unchanged; equal redeployment -2.2..-3.2%
  per dollar; state-conditioned redeployment -1.5%..+0.3% per dollar with block sign flips);
  intraday segment diagnostic (exit at 09:40 -1.48%/day vs -3.94% hold; loss per unit of exposure
  worst in the first 30 minutes); de-risk diagnostic (unconditional 50% cut at the completed
  ET600 bar +73 bps/day at N=2, damaged-only +49); harvest diagnostic (sell 100% at the first
  +30% touch +89 bps/day, block 2 much stronger than block 1); touch-fade path scan (median +30%
  toucher gives back -10.8% from the touch-bar close to the close; only 30.6% continue). Artifact
  paths: `factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/` and the run directories named
  there.
* **Interpretations (to be interrogated, not inherited):** "the right tail is a touch phenomenon,
  not a hold phenomenon"; "sell into strength"; "10:00 is the checkpoint that matters"; "+30% is
  the exit"; "staged capital failed"; "holding runners is wrong"; "the improvements are only
  de-leveraging"; "the morning segment is the worst per unit time". Each is a reading of the
  evidence above and may be wrong or incomplete.
* **Rulers (never conclusions):** any fixed clock time, +30/+50/+100 thresholds, stop levels,
  retained-MFE fractions, N, and the F1-F14 family structure.
