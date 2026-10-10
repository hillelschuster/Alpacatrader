# STATE — current truth (latest update 2026-10-09). Full chronicle: factory/STATE.md (append-only log).
# Hypothesis ranking + falsifiers: researches/HYPOTHESES.md (living).
# History before 2026-09-04: researches/CANONICAL_STATE.md (superseded snapshot, kept for trust map).

## CURRENT 2026-10-08 — H025 print-resolved NEGATIVE (do not arm); new independent
## open-anchor program is the live research lane

**H025 flush-bid — historical pass vs print-resolved failure.** The 2026-09-13 snapshot
below ("OOS-PASS-PAPER": pf>=2 n=381, +1.14%/trade net 100bps over 2024-01..2025-02) is
kept as HISTORY and is non-executable evidence: the frozen engine credited exit-at-c0
whenever the fill bar's high tagged c0. Resolving every fill against local NET SIP
prints (worktree `basket-phase2-f1`, 2026-10-06) shows the fill minute usually OPENS
at/near c0 and collapses through the resting bid — only ~10-12% of ambiguous same-bar
target fills printed >=c0 AFTER the fill. Resolved net/fill (100bps, 1046 allowed
days): pf2-preorder +0.66% legacy -> -1.72% resolved (pessimistic bound -1.86%);
all-orders +0.09% -> -2.27%; negative in every year 2021-2026. Follow-ups all negative
(exit surface 0/29 policies positive; PRE-REG-STABILIZE-01 confirmation 32/32 cells;
resolved-fill pocket scan no positive bin; entry-discount surface negative at every
depth; qualification curves 0/13). **H025 route CLOSED — do not resurrect, freeze or
forward any variant; the paper bot must not be armed on this rule.** Bot status: no
live flag or order was changed by this program (flush_bot is not running; LIVE+KILL
flags exist in this checkout — cleanup/arming is the user's call). The 2026-09-13
"H025 = OOS-PASS-PAPER" block below is retained as historical evidence only.

**Canonical Oct-6 truth source (actual worktree).** `/home/hillel/algo projects/
worktrees/Alpacatrader/basket-phase2-f1` (HEAD 203ddaf "Entry + qualification
surfaces complete: honest lens negative at every depth; H025 route closure
finalized"). Evidence `factory/artifacts/h025_research/`, producers
`factory/scripts/h025_research_*.py` (core daily replay with exact published parity
729/729 + 851/851 fills, 0.0 diff; chronology NET-print resolution; entry/exit/
execution/qualification sweeps). This root's September entries are stale on H025
status; the worktree governs.

**New independent program (this checkout) — open-anchored top-gainer research.**
Separate code, separate data root, no dependence on the dead flush rule, and no
previous-close features (avoids the denominator defects recorded in the worktree).
Producers: `factory/scripts/alpha_{open_panel,open_sim,open_events,open_overnight,
open_learned,sequence_payoff,proven_push,short_diagnostic,quote_audit,micro_core,
micro_flow,micro_reclaim}.py`. Contract/panel/outputs root: `~/alpha-data/
open-search-v1` (`contract.json`: admission = full-PIT SIP B snapshot top-10,
gain>=5%, $1 floor, fresh price; 5-minute causal clock, OHLCV strictly et<t, last
actual bar must be t-1; entry = minute-t open proxy — a minute with no bar leaves
the signal unfilled in cash; fixed-time exits at first actual open >= entry+h;
unresolved exits stay UNKNOWN and are never dropped; round-trip costs 100/150/200bps).
Blocks: fit 2021-02..2022-12, validation 2023, out-of-fit confirmation
2025-02..2026-05 — previously explored market periods, NOT a pristine holdout.
Protected, NEVER read: 2024, 2025-01, 2026-06..2026-08.
Replay (all in this checkout, defaults point at ~/alpha-data/open-search-v1):
`python factory/scripts/alpha_open_panel.py` (panel build) then per family
`alpha_open_events.py`, `alpha_open_overnight.py`, `alpha_open_learned.py`,
`alpha_sequence_payoff.py run|predict`, `alpha_proven_push.py`,
`alpha_short_diagnostic.py`, `alpha_micro_flow.py --stage all`,
`alpha_micro_reclaim.py run|report`, `alpha_quote_audit.py --trades <file> --out <dir>`.
Execution honesty: minute-open proxy only — quote support is NOT a guaranteed fill;
partial/unexplained `exit_qty` stays UNKNOWN; a fee-funded floor uses
budget/(ask*(1+s)); any candidate positive only at low residual cost or with
UNKNOWN rows present is reported as such, never as a pass.
Per-study results are published ONLY from the generated packet
`factory/artifacts/alpha_search_20261008.json` — the BASE-WAVE outcomes table is
directly below and is mirrored in the CURRENT section of researches/HYPOTHESES.md,
HANDOFF §17 and the factory/STATE.md 2026-10-08 entries.

**Data/storage facts.** SIP full-PIT inputs live on the shared Windows mount (C: with
~24Gi free); outputs and bulk per-day files go to Linux `/home/hillel/alpha-data/
open-search-v1` (script defaults deliberately avoid the nearly-full data mount).
Publish producer + proof artifacts; never bulk day data.

**Boundaries held by this documentation pass:** no protected/sealed read, no FREEZE,
no deploy, no bot flag/order change, no live trading action.

### 2026-10-08 BASE-WAVE packet outcomes (all numbers from
### `factory/artifacts/alpha_search_20261008.json`, status COMPLETE, sha256
### 8887fb1e74ff1abdb763a4c3f9aad332f2cefc1a34c53b4ce709d11b071ff157; asof 2026-10-08)

Ten registered studies (8 base families + rare sparse extension + bid-backed burst);
panel = 1,066 days / 1,076,304 rows (train 484 / validation 250 / confirmation 332).
Book $3,000 = 3 x $1,000 slots; micro book $750 = 3 x $250 orders. `%/day` =
mean_daily_lower_bound_book_pct; `%/order` = mean_net_known_fill_per_order_pct.

| study | verdict (packet headline) | key readings | status |
|---|---|---|---|
| events | FROZEN_DIAGNOSTIC_NO_EDGE | val chosen squeeze_release_h390 -0.2429 %/day @100 (n=352 known, 0 unk); late -1.7345 %/order (n=543 known + 8 UNKNOWN) = -1.7489 %/day; -2.0161/-2.2819 %/day @150/200 | all-known-negative |
| learned | frozen h15 thr0.01 | val +0.0926 %/day @100 (n=208, CI [-0.3028,+0.5871]); late -0.2922 %/day @100 (n=447, CI [-0.7199,+0.1619]), -0.5146 @150, -0.7359 @200; both quote audits whole_portfolio_certified=false (val 132/208, late 218/447 pairs UNKNOWN) | val-positive subset only; late negative |
| sequence | no_selected_head_positive_on_late_block | late h60/scale30 -0.3752 %/day (n=235, 172 traded days); h15 -0.6057 %/day; TCN encoder fit unsupervised on all 734 fit-block days 2021-02..2023-12 (validation exposure) | negative |
| proven_push | negative_at_all_cost_rungs | late -1.8365 %/order (n=1680) = -3.0976 %/day @100; -3.9251 @150; -4.7458 @200 | negative |
| short_diagnostic | CONDITIONAL_SHORT_DIAGNOSTIC_NOT_EXECUTABLE_ALPHA | per-order unit (NOT a book %): train +1.1562 %/order (n=377), val +2.257 (n=119), late -1.5353 %/order (n=339 known + 4 UNKNOWN) @100; borrow/SSR/locates/margin unverified | late negative |
| overnight | negative_at_all_cost_rungs | chosen qA late -1.1466 %/day @100 (n=954 known + 4 UNKNOWN); -1.6221 @150; -2.0952 @200; val -2.6414 %/day | negative |
| learned_sparse_extension (rare) | DISCOVERY-NOT-VALIDATED | h60 thr0.03: val +0.0907 %/day (n=61, CI [-0.2759,+0.5043]); LATE +0.1417 %/day @100 (n=143, 124 traded days, 8/16 months, +0.9869 %/order), +0.0694 @150, -0.0026 @200; exploratory sub-floor; quote audit covers 77/143 pairs | ONLY positive lead; not validated |
| micro_flow | no_edge_at_any_cost | late -1.0333 %/order @0 residual (-0.5577 %/day), -2.0181 @100 (-1.0891 %/day); 58 dev dates UNKNOWN (raw '?' quote flags) -> development means are worst-case lower bounds | negative |
| micro_reclaim | not_positive | late -0.4533 %/order @0 residual (-0.4636 %/day), -1.4438 @100 (-0.8986 %/day), 1 UNKNOWN | negative |
| bid_backed_burst | DIAGNOSTIC not promoted (chosen 15s horizon least-bad) | val @100 -0.8203 %/day (CI [-1.1788,-0.5876], 0 unknown days); late @100 -0.9803, @150 -1.2225 %/day | negative |

**Best candidate mechanism (exact):** `learned_sparse_extension` h=60,
thr=0.03 — the sparse LightGBM payoff model (26 causal open-panel features, no
previous-close features) firing on its rare tail: late block +0.1417 %/day of the
$3,000 book at 100bps on 143 known fills over 124 traded days (+0.0694 %/day at
150bps, -0.0026 at 200bps), 8/16 months positive (worst measured month -1.1858
%/day of the book, 2026-02 — a real regime/tail risk, reported, not a kill).
DISCOVERY-positive, RETAINED, NOT validated: day-bootstrap CI includes zero; the
30<=known<100 window was the exploratory lane's initial selector floor
(traded_days>=30) — a lane-eligibility criterion, not a one-number veto: the late
block already carries 143 known fills over 124 traded days, inside the primary
lane's power range. The late block was previously explored (not pristine), and
the as-of side-aware quote audit covers 77/143 pairs (whole_portfolio_certified=
false; the other 66 pairs are UNKNOWN, not cash). Do not label it closed/failed;
promotion still requires the standing power doctrine (>=6 months pooled
development + >=2 pre-registered unseen collision months at 100bps) cleared on
pristine data, so do not promote it yet.

**Annualization (exact; from the packet's own annual_ev_source_facts):** service
is 252 regular US sessions/yr (not 24/7); fills/yr = (143/332) x 252 = 108.542;
x $1,000 order x +0.9869 %/order = $1,071.196/yr before operating costs and tax
= 35.7065% SIMPLE of the $3,000 book (NOT CAGR). The $2,510.991/yr all-fills
figure holds ONLY if the 77/143 quote-covered pairs (mean +2.5716 %/order at 0
extra, +2.3134 at 25 extra) represent all fills — representativity NOT confirmed;
the 66 uncovered pairs stay UNKNOWN, not cash. Stress = the measured 100/150/
200bps rungs (1000bps was never used; no one-number sample/CI/cost-stress veto).
Official SIP = $99/mo = $1,188/yr unless already covered.

**Precise missing data to promote:** (1) whole-portfolio side-aware quote audit
(currently 77/143 pairs) plus capacity/queue realism at order size; (2) a pristine
unseen holdout block (2024 / 2025-01 sealed; 2026-06..08 protected, never read);
(3) forward/unseen validation under the standing power doctrine (>=6 months pooled
dev + >=2 pre-registered unseen collision months at 100bps) on a pristine
block — the >=100-known-fills figure is the historical lane-selector floor
(primary vs exploratory), not a blanket veto; (4) exit/stop-risk and
re-entry-frequency evidence — NOW DELIVERED by the 2026-10-09 NIGHT-WAVE packet
(`factory/artifacts/alpha_night_extensions.json`, sha256
e5d77d39cbaf90c5981c38a696eafed09ec9d963a74858a40d0bbe8345f8e683; the four former
IN_PROGRESS workers — quote/service + execution frontier; stop-risk; daily
frequency/re-entry h15/h30; supervised micro 5/15s — are the completed research
wave whose outcomes are directly below; absence is NOT a negative and the full
validation objective stays active); (5) for a yearly 24/7 service framing, an
official real-time data feed (Alpaca SIP, $99/mo, market-data docs updated Oct 6) —
the current service is regular US sessions only (252/yr).

**Replay (cwd = repo root):**
`uv run --no-sync python factory/scripts/alpha_search_report.py --root ~/alpha-data/open-search-v1 --out factory/artifacts/alpha_search_20261008.json`
regenerates the packet; per-family producers (same `uv run --no-sync python ...`
prefix): `alpha_open_events.py --panel-root ~/alpha-data/open-search-v1 --out ~/alpha-data/open-search-v1/events`;
`alpha_open_learned.py --out ~/alpha-data/open-search-v1/learned` (replay:
`--horizon {15,60,390} --threshold {0.01,0.03,0.05} --period {train,validation,confirmation} --cost 100`);
`alpha_sequence_payoff.py run --npz data/atlas/sequence/v1/runs/causal/block/embeddings.npz --out ~/alpha-data/open-search-v1/sequence`;
`alpha_proven_push.py --panel ~/alpha-data/open-search-v1 --out ~/alpha-data/open-search-v1/proven_push`;
`alpha_short_diagnostic.py --panel ~/alpha-data/open-search-v1`;
`alpha_open_overnight.py --out ~/alpha-data/open-search-v1/overnight`;
`alpha_sparse_model_extension.py --panel ~/alpha-data/open-search-v1 --out ~/alpha-data/open-search-v1/learned_sparse_extension`
(+ mandatory `alpha_quote_audit.py --trades .../trades_confirmation_100.parquet --out .../learned_sparse_extension/quote_confirmation --latency-ms 250 --max-age-s 2.0 --order-budget 1000`);
`alpha_micro_flow.py --stage all --out ~/alpha-data/open-search-v1/micro_flow`;
`alpha_micro_reclaim.py report --out ~/alpha-data/open-search-v1/micro_reclaim`;
`alpha_bid_backed_burst.py --command report --out ~/alpha-data/open-search-v1/bid_backed_burst`.
Models: `learned/models/payoff_h{15,60,390}.joblib` + `feature_order.json`
(api: `alpha_open_learned.load_models/score_frame/feature_matrix/make_signals`); sequence heads
`sequence/models/head_scale{30,60,120}_h{15,60}.joblib`.

**Execution risks (standing):** six families use a minute-open execution proxy — a
quote-supported touch is NOT a guaranteed exchange fill and any promising candidate
needs as-of side-aware quotes plus a size review; micro families' ASK@signal+250ms /
BID@target touches are capacity-checked at $250/side but still not fill guarantees;
0bps residual on micro lanes is a diagnostic, not a free fill; UNKNOWN exits are
charged -100% of the order budget in the lower bound (never dropped, never cash);
no-fill intents keep cash and are charged no fee (not losses, not cash returns); the
2025-02..2026-05 confirmation block (and 2025-03..08 micro months) was previously
explored — NOT a pristine holdout; short lane borrow/SSR/locates/margin/buy-in all
unverified; micro_flow's 58 UNKNOWN dev dates make its development means lower
bounds; micro book assumes margin-style funded cash reuse (not a $750-cash-account
claim). No live flags/orders changed; nothing here is a deployment.

### WAVE-2 (2026-10-09)  -  real-quote battery on the full acquired quote universe

Source of record: `factory/artifacts/wave2_alpha_search_summary.json` (sha256
17515f55be035139ef1b731a03e01a8b4ac0cb9368e34d590568671a2f170273; 10 lanes, each with its
verbatim decision text and honest note). Acquisition is now complete: quotes for the full
admitted PANEL universe across all 1,003 sessions  -  train 421 (2021-05..2022-12),
validation 250 (2023), confirmation 332 (2025-02..2026-05, previously explored, NOT
pristine)  -  via `alpha_quote_universe_acquire.py --universe panel --resume --blocks
<block>`. Reference row kept: the real-quote core lane theta.030/h60/d0 measures late
+3.38 $/day at 25bps on the $750 book (131 known fills); the frozen minute-proxy
retained rare h60 cell stays the program lead (+$1,430/yr simple on the $3,000 reserve).

**CLOSED NEGATIVE on real quotes** (do not reopen without a genuinely new mechanism):
- opening-range pullback (alpha_opening_range_pullback.py): all six views negative on
  both blocks at every rung; best validation pullback_retest@15m -1.21 $/day at 25bps
  (233 known fills, 13 UNKNOWN), best late -4.40 $/day.
- liquidity recovery after flush (alpha_liquidity_recovery.py): DIAGNOSTIC, nothing
  promoted; least-bad flip_first_positive_1s hold900 val -0.31851 / conf -0.35026
  fraction of book/day, 0/9 positive confirmation months, 656 known + 173 UNKNOWN fills
  (artifact re-verified 2026-10-09 from .../liquidity_recovery_five_min/summary.json;
  534/564 built days coverage-complete after the supplement-stream repair).
- multi-session carry (alpha_multi_session_carry.py): NO_EDGE_EVIDENCE  -  a dead gate,
  not a robustness statement; only 3 funded intents per view across 397 pre-freeze
  candidate days, all exits UNKNOWN, every view $0.00/day.
- cross-name rotation (alpha_cross_name_rotation.py): negative OOS  -  chosen
  lgbm_reg_h15_thr50bps validation +0.67 $/day (115 known) but late -1.26 $/day (277
  known); touch-only baselines -61.15 validation / -62.31 late $/day. The lgbm_rank
  family produced 0 fills at every bar (calibrated rank scores never cleared the bars):
  a limitation, untested by construction.
- hour-of-day learner (alpha_touch_hourly_payoff.py): both blocks negative; the chosen
  h15_net100bps validation partial value is +0.098 $/day over 25 known fills with a
  whole-basis -3.90 $/day and every other view negative; late -1.07 $/day (86 known).
- h390 whole-day hold on validation (alpha_retained_h390_lane.py): all 12 cells negative
  on 2023 validation (best -11.56 $/day) against the frozen repeat_h60 reference (VAL
  +4.88 / late +9.28); late chosen once_d2_thr050 +12.01 $/day and non-chosen d2_thr030
  +37.21 $/day (426 known) - re-verified 2026-10-09 from
  .../retained_h390_lane/results.json (producer re-run, 56.2s, default out).

**LEADS ONLY (not validated, not promotion grounds):**
- passive mid resting entry + TTL (alpha_passive_quote_confirmation.py): chosen
  h60_thr200bps_mid_ttl120s validation +4.66 $/day, late +8.59 $/day at 25bps on the
  $3,000 book (45/105 known fills; non-chosen late ttl30 +12.15 $/day)  -  falsifier is
  the UNKNOWN share: 57.7% of validation and 54.0% of late attempts are UNKNOWN, the
  full-loss lower bounds are -415.34 / -560.69 $/day, and a resting quote-supported
  fill is not an exchange-fill guarantee.
- adaptive depth participation (alpha_adaptive_depth_sizing.py): chosen
  h60_thr300bps_part50pct validation +1.36 $/day (53 known, 8 UNKNOWN = 12.9%), late
  +5.95 $/day (122 known of 142 attempts over 113/332 traded days; 10/16 months
  positive; +$16.19 mean net per known fill; worst known fill -$263; bootstrap p>0 =
  0.868, CI95 -4.71..+16.93 $/day on the $3,000 reserve whose mean deployed capital is
  only $314/calendar day)  -  falsifiers: cost fragility (already negative at the 125bps
  rung on validation, -0.10 $/day), the 14.1% UNKNOWN share, and the full-loss lower
  bound -54.29 $/day; displayed depth is capacity visibility, not a fill guarantee.
- the retained core lane (theta.030/h60/d0) remains the reference row above.

**No new lane is validated.** A validation-negative / late-positive split (h390 hold,
passive mid, adaptive depth) is regime evidence across previously-explored periods, not
promotion grounds; the late block is not a pristine holdout, UNKNOWN-heavy positives
are not firm, and the four night-extension workers (quote/service + execution
frontier, stop-risk, daily frequency/re-entry h15/h30, supervised micro 5/15s) remain
IN_PROGRESS. No live flags/orders changed; no deployment.

### 2026-10-09 NIGHT-WAVE outcomes — four extensions COMPLETE (all numbers from
### `factory/artifacts/alpha_night_extensions.json`, 857,352 bytes, sha256
### e5d77d39cbaf90c5981c38a696eafed09ec9d963a74858a40d0bbe8345f8e683; status
### COMPLETE-4-OF-4-EXTENSIONS, kind ALPHA_NIGHT_EXTENSIONS_NOT_GOAL_COMPLETION, asof
### 2026-10-09; every program DISCOVERY-NOT-VALIDATED and the packet's goal OPEN)

The four night-extension workers named in the BASE-WAVE entry above are this completed
research wave; their results were NOT in the base packet and this packet is not a
self-issued goal completion. The base packet is referenced by path+SHA only
(8887fb1e…, 832,676 bytes). Best retained cadence estimate = highest full-cohort
minute-open-proxy net $/year among the tested $1,000-ticket cadence views, with
frequency beside it; actual quote support remains partial. **sparse_daily repeat_h60,
+$1,430.80/yr proxy @100bps (rank 1)** precedes the once-a-day base proxy at
+$1,071.196/yr (rank 2; quote-supported 77/143). BEST MEASURED, not global-best, not
live-filled, not a guaranteed/projected profit.

| extension | verdict | key readings (val 2023 = 250 days; late = 2025-02..2026-05, 332 days, NOT pristine) | status |
|---|---|---|---|
| sparse_daily (re-entry, SAME immutable `learned/models/payoff_h60.joblib` sha c5493c6c…, 26 features, no refit) | repeat_h60 validation-positive, DISCOVERY-NOT-VALIDATED | chosen on val $/day @100bps: repeat_h60 +$2.8387 > control once_h60 +$2.7222 (exact reproduction of the original +0.000907/day, 61/61) > repeat_h30 +0.9254 > repeat_h30_strong -0.4563 > repeat_h15 -1.1317; val repeat_h60 67 known fills / 55 traded days; LATE +0.0018926 of the $3,000 book/day = +$5.6778/day = **+$1,430.80/yr simple x252**, 157 known fills / 0 UNKNOWN on the SAME 124 traded days (+0.000305 @200); added legs vs first-attempt baseline +14 fills net **+$33.84/leg** (+$1.4270/day, +$473.76 total); repeat_h30 legs -$9.23/leg, repeat_h15 val-negative, repeat_h30_strong 6 val/14 late attempts; quote audit of the NEW cohort 85/157 supported (mean +2.7513%/fill @0 extra, +1.7249 @100), 72 UNKNOWN, whole_portfolio_certified=false | BEST MEASURED lead; not validated |
| sparse_execution_frontier (actual ASK/BID touch, L1 capacity) | DISCOVERY-NOT-VALIDATED | late @$1,000/250ms/2s: 143 intents, 110 priced, 79 L1-supported (55.25%), 64 UNKNOWN; covered mean +2.5102% = +$25.10/fill (win 40.5%, PF 1.379, worst -30.26%), CI95 [-2.02%,+7.19%] includes zero; observed covered contribution $1,505.23/yr, $317.23/yr net of the $1,188 SIP plan; $2,724.66/yr whole-case is CONDITIONAL on unknown-matches-covered (not confirmed); val61 41/61 supported, $111.03/yr covered; latencies 250/1000/2000/5000ms each priced on its own (no best-latency pick), 15s stale leg = corroborated scenario only; sizes $250..$5,000; integer qty=0 corrected to a KNOWN no-order cash class; all 8 missing legs acquired read-only (HTTP 200: RKDA 2023-12-28, RPGL 2026-01-30, JCSE 2026-03-30, CODX 2026-05-26) merged over the ranked cache | quotes measure touch cost, not fills |
| sparse_exit_management (paired hold60m vs stop10/stop15) | DISCOVERY-NOT-VALIDATED — keep the BASE hold60m | val selection @+25bps unknown-full-loss coding bound (frozen before late outcomes): hold60m -0.0383711; stop10 -0.0374688 (highest bound); stop15 -0.0407160 — not measured whole-portfolio EV; paired val 31 known pairs: worsened 10 vs improved 8, delta mean -0.79% (median 0); late stop10 whole-book bound -$38,069.40/yr is a SYNTHETIC unpriced-UNKNOWN full-unit charge (known-covered annual contribution -$876.63/yr) — NOT observed PnL, NOT expected loss; stops clamp the tail (control worst-5 -32.2% vs policy -10.3%) but cut recoveries (25 stop-through gaps, mean -100.1bps, worst -368.1bps) and the covered actual stop is worse than control; base reproduction exact (val +0.0009073964, late +0.0014169262); late control 92 known (resting-until-next-regular-quote protocol) vs base 77 (instant protocol) — annotated, never blended | stop risk priced, no promotion |
| micro_payoff (supervised 5/15s scalp learner) | no_edge_at_any_cost for THIS fixed model | fit 2021-05-03..10-29 (127 days, 214,506 states/head, clip [-0.2,0.3] fit-only); chosen h5 thr0.001 BEFORE any late record — validation EMPTY (0 signals/0 fills over 250 days = actual no evidence, NOT a measured zero edge); late 187 days: 9 signals, 8 fills on 6 traded days, mean daily net -$0.0439 @0 residual (-$1.03/fill, win 37.5%, PF 0.396), -$0.0546 @10, -$0.0705 @25, -$0.1499 @100; 0/9 positive months; CI includes zero | measured-not-forced; no universal scalping-impossibility claim |

**Everyday goal — MEASURED, not forced.** The repeat cadence raises the SAME frozen
model's frequency from 108.542 to 119.17 fills/yr ((157/332)x252) while keeping the
identical 124 traded days of the 332-day late block (~10 fills/month, not daily). The
fixed supervised micro model did not produce frequent profit (8 fills over 187 late
days, negative at every residual rung) — a falsification of that formulation only;
no universal "all scalping is impossible" claim is made.

**Mechanism of the retained lead (descriptive profile; producer
factory/scripts/alpha_retained_mechanism.py, runtime 20.2s, output
~/alpha-data/open-search-v1/alpha_retained_mechanism/profile.json sha256
4d6d23d8ef73e9a14d552bf9cb4cce85d806612f4a9c4c4a2641bb509f7b8230):**
deep-drawdown recovery inside a strong intraday uptrend, NOT positive chase — val
medians gain_open +62.7% vs open, -15.2% off the day high, +12.8% vs VWAP, ret3
+0.50% / ret15 -2.26%, dv_accel 1.33x, ~33min off the high, ~80min after admission,
rank 1. Calibration is GROSS vs GROSS: predict_gross 3.688% vs realized_gross 2.132%
(val) / 3.657% vs 2.002% (late) — error ~+1.6pp, never compared with net
(+1.116%/+0.987%). Coverage is endogenous (late quoted-77 +2.111% vs unresolved-66
-0.325%), the entry ASK sits above the bar open (mean +29.6bps) and the 77-pair
identity is exact (price effect -0.592pp, idle +0.026pp, net difference +0.202pp,
fee wedge +0.768pp). TOD/price-band splits are descriptive only — no new post-hoc
filter, training or picks.

**Cash-reuse/PDT framing (CURRENT Alpaca docs, cited 2026-10-09):** per
https://docs.alpaca.markets/us/docs/understanding-the-new-intraday-margin-rule
(updated 2026-04-27) pattern-day-trader designation, the 4-trades/5-days limit and
the $25,000 minimum are REMOVED; non-leveraged margin accounts re-enter without T+1
locks while sufficient-equity/house intraday-buying-power rules still apply (PDT
account/config fields removed per the 2026-07-06 changelog,
https://docs.alpaca.markets/us/changelog/2026-07-06-pdt-db49dba). The research books
($3,000 open / $750 micro) assume margin-enabled funded cash reuse; real house/asset
eligibility is NOT verified, cash-account settlement is not modeled and no account
CAGR is claimed. Do NOT re-apply the old $25k floor or 4/5-day cap.

**Standing records held:** UNKNOWN outcomes counted and charged the producer's
full-unit lower bound, never cash, never dropped; known no-orders keep cash and are
never losses; the -$38,069.40/yr exit bound is a synthetic unpriced-UNKNOWN
convention, not expected loss; annualization is a simple x252-session rate, never a
CAGR; no arbitrary 100-fill/CI/cost-stress veto (100bps is the baseline bookkeeping
rung); no live flags/orders changed; nothing here is a deployment. Full chronicle
entry: factory/STATE.md 2026-10-09; replay commands (packet producers, chosen repeat
and original model, quote/capacity/RTA audit) are listed there and mirrored in
HANDOFF §18. News/catalyst access repair (AAPL 2023-01 = 2 articles; RKDA
2023-12-27..29 = successful empty; PIT revision limits): artifact
factory/artifacts/alpha_news_access.json — metadata availability verified, no
catalyst alpha inferred.

## Where we are (2026-09-13) — HISTORICAL snapshot (superseded by the CURRENT
## 2026-10-08 section above; retained for the trust map)
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
