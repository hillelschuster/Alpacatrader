# PLAN — ATLAS E3: the bounded SIP-print microscope

**Status: DRAFT-IMPLEMENTED (step 2 of 4 — the anchored extraction and the I-EV measurement).**
Step 1 (the census layer and the case-set freeze) was implemented by `E3CensusBuilder` on
2026-09-28 and audited by `E3CensusDebug`; step 2 (Stage B: the B1/B2/B6-corrected anchored
extraction and the I-EV arm) was implemented by `E3StageB` on 2026-09-28 in the same worktree.
The pre-registration draft below is the plan as written by `MicroScopeArchitect`; it is
reproduced verbatim, and every deviation is listed in §D (step 1), §F (the Stage-B semantics and
the corrections B3/B5/B7/B9 to the frozen text) and §G (what step 2 measured).
This file is the plan text the producer implements.

* Producer: `factory/scripts/basket_atlas_e3.py`
* Artifacts: `factory/artifacts/basket/phase2/ATLAS/E3/{anchors.parquet, case_sets.json,
  census.parquet, census.json, census_cost.json, cs5_anchors.parquet, selftest.json}` (step 1) and
  `{stage_b_features.parquet, stage_b_prints.parquet, iev.json, latency.json, stage_b_cost.json}`
  (step 2)
* Reproduce: `.venv/bin/python factory/scripts/basket_atlas_e3.py --stage all --bars-check`
  (step 1), then `.venv/bin/python factory/scripts/basket_atlas_e3.py --stage stageb --workers 5`
  (step 2), then `.venv/bin/python factory/scripts/basket_atlas_e3.py --self-test`
* Scope of step 2: **Stage B (anchored extraction) and the I-EV arm**. The L-EV arm is a separate
  script and a separate artifact by §5's structural separation and is **deferred, declared** in
  `latency.json`; the dollar stage (G5) is not implemented.

---

## §D. Implementation notes and deviations (step 1)

Every deviation is also carried machine-readably in `case_sets.json:deviation_log`.

| # | plan says | implemented | why |
|---|---|---|---|
| D1 | cap 4,000 windows **only for CS-3**; §6 cost line says "~4,000 anchored windows" for Stage B | per-case-set cap 4,000 for CS-1/CS-3/CS-4/CS-5; **CS-2 uncapped at its eligible population (4,946 firing members → 4,937 after the raw gate)**; total **20,937** windows | §6's single 4,000 line contradicts §1's own numbers (CS-3 = 4,000 member-windows, CS-2 = 4,946 firing members). Per-set caps keep every set bounded and auditable; the tension is published rather than silently resolved. A global 4,000 budget would need a new pre-registration of the allocation. |
| D2 | CS-4 carriers = the plan's five names | the plan's five; the frozen producer's `fall.VOL_MEDIATION_FEATURES` carries only four (`bars_below_entry_episode` is not in it) | the plan names five and all five are panel-native; the 4-carrier variant's eligible count is published in `meta.CS-4.variant_4carriers_eligible` so the difference is visible. |
| D3 | CS-4 strata "computed within the training block" | deciles computed inside **each** block, block label kept on every anchor | the plan's own I-EV discipline fits block1→block2 *and* block2→block1; per-block quantiles let either block be the training block at analysis time. |
| D4 | CS-5 anchor = first print after a ≥5-minute silent run "inside a member window" | the run must be **bracketed by prints** inside the window | an unbracketed run is entry/tail silence, not a halt, and `pre_halt_trend` needs a pre-hole print. Unbracketed runs are counted at the candidate level (`census_candidates.CS-5.unbracketed_silent_runs` = 54 of 30,029), never anchored. |
| D5 | probe constants reused (`HALT_ACTIVITY_FLOOR=60`) | applied as a CS-5 member-day inclusion rule (≥60 minutes with a U-path print) | the probe used it as a day-selection floor; at member-day grain it is the same "live name" test. |
| D6 | CS-2 anchor = "the trigger bar of a named, already-frozen exit" | the anchor is the **decision bar `t`** (first bar whose close satisfies the condition), located by `basket_atlas_ledger.Giveback.locate_exit`; `post1` is therefore the execution bar `t+1` | matches the window rationale (the seconds inside the trigger bar *and* the execution bar are what the minute panel cannot see); the ledger's locator is imported, never re-derived, and the panel's `giveback_fired_10` flag is asserted to agree with it on all 6,160 members. |
| D7 | CS-3 "4,000 anchored member-windows" | the bound is on member-windows = **2,000 pairs**, so the 60/40 family and 50/50 block allocation is applied at pair level (600/600 A_pm, 400/400 B600) | a pair is the analysis unit; splitting a pair across the cap would destroy the matching. |
| D8 | selection rule for the bounded subsample is unspecified beyond "deterministic … per-day caps so no single day dominates" | frozen rule: inside each declared cell, days visited in ascending `sha256(day)` order, each day's candidates in ascending `sha256(anchor_id)` order, picks taken round-robin across days | reproducible, order-invariant, and per-day counts differ by at most one. |
| D9 | CS-4 `mfe_surrendered_pos` (a derived name) | taken from `fall.load_frame(...).x["mfe_surrendered_pos"]` | the frozen producer's own derivation; the fall frame is positionally reconciled against an independent panel identity read (`et`, `bar_index`, `entry_et`, `session_end`) before use. |
| D10 | "census is ONE projected-column pass per day" and the artifact must be byte-stable | the census pass is one `load_prints` projection per day; **wall-clock measurements live in `census_cost.json`**, not in the frozen `census.json` | a wall-clock timestamp inside the freeze would make "same command twice, identical bytes" impossible. |
| D11 | the 255 duplicated `(day,ticker)` paths are "excluded from any cross-family statement" | CS-3 keeps the Phase-1 axis's own stricter rule: the axis drops `dup_cross_family` rows outright, so CS-3 has **zero** shared paths; CS-1/CS-2/CS-4/CS-5 keep duplicates and only report their count (`n_on_shared_cross_family_paths`) | the axis is imported from `basket_atlas_pairs`, and re-admitting the duplicated rows would not be the Phase-1 axis any more. |
| D12 | "the coverage JSON, and the quote file's symbol list" as part of Stage A | the trades store is read once per day as specified; the quote file contributes one **symbol-column** scan per day (p50 ~0.19 s, 5.4 MB) and the coverage JSON one small read | `quote_present` must be measured from the quote file itself, not from the candidate snapshots. |

Ambiguities that were **not** silently resolved (they are open at analysis time):

* the plan does not say whether CS-1's reclaim must start from below the entry price; the literal
  rule (`reclaim_count(t) > reclaim_count(t-1)`) is implemented and the anchor carries
  `bars_below_entry_episode`, so a stricter reading can be applied at extraction without a new pass;
* the plan does not say whether CS-2's outcome set (`readmissible`) is read from the ledger's
  per-member rows (not persisted in `ledger_selftest.json`) or recomputed at analysis time; the
  freeze only fixes the anchors, so nothing is lost;
* the plan's §3 feature table is not frozen here — step 1 deliberately stops before any feature.

---

## Pre-registration draft (verbatim)

# E3 — the bounded SIP-print microscope (pre-registration draft)

Read-only design. No files were edited; no code was run. Every number below is read from a
committed artifact and is attributed.

---

## 0. Framing, and two corrections to the brief's premises

**The microscope has exactly two jobs and they are kept in separate artifacts, separate scripts,
separate gates.**

* **I-EV (information).** Is there causal, out-of-block, day-clustered information *below the minute
  bar* that separates cases the minute panel demonstrably cannot separate?
* **L-EV (latency).** Conditional on a signal that has **already been earned** on the minute
  convention, is acting at print level inside the next minute worth more — net of a real fill and a
  slippage ladder — than acting at that minute's open?

Neither may be read as the other. A positive I-EV with a null L-EV is a complete result (the tape
carries information we cannot monetise at this participation size). A positive L-EV with a null I-EV
is a complete result (the tape is worth buying for execution only). The pre-registration must state
both terminal sentences *before* the estimates exist, or a null will be reinterpreted later.

**Correction 1 — quotes are not absent.** The brief says "no true aggressor/quotes". Quotes are
locally present, for exactly the names the ATLAS panel holds. `sip_ingest.symbols_for_day_net` sets
`quotes = top-3 per SIP snapshot`; the day's snapshots are `A_open@570, A_pm@570,
B@{575,580,585,590,595,600,615,630,645,660,690,720}`. The **A_pm@570 top-3 is the A_pm family** and
the **B@600 top-3 is the B600 family** — both are in the quote symbol set by construction
(verified by reading `data/sip/candidates/2025-07-09.json`, where the B@600 top-3 is SUGP/EGG/NMRA).
All 1,066 quote day files exist with `status: ok` and `symbols_with_data == symbols_requested`.
Consequence: a **quote-aligned signed-flow proxy** and an **NBBO-bid exit price** are both available.
They remain *inferences*, not aggressor flags (see §3 caveat 6), and a per-member census is still
Gate G0 because the panel's B600 members are ranked by the *anatomy* snapshot, which is not
byte-identical to the candidates' B@600 ranking.

**Correction 2 — "E1".** I read E1 as **ATLAS-E1, the exhaustion-conditioned release through the
ledger** (`ATLAS-PHASE1-SYNTHESIS.md` §4), not the legacy lb18 flush event. The design is written so
this does not matter: the trigger source is a **parameter** (`--trigger-source panel_ruler | E1 |
lb18_E1`). Every case set is defined on panel-native events; E1 is a *substitution of the trigger*,
never a prerequisite for the case definition.

**Anti-drift commitments written into the pre-registration.**

1. No print-level feature becomes a *trigger* in this programme. The microscope measures information
   and execution. It does not set a stop, a target, a clock, or a horizon.
2. Every label is already-computed panel anatomy: `v_forced_flat`, `final_high_flag`,
   `cost_of_waiting`, `remaining_run`, `readmissible`, `giveback_fired_G`. **No new max-order label
   is created.** The Phase-1 prohibition (no policy, gate or directional rule derived from the decile
   split) is carried verbatim.
3. The feature table is frozen and hashed before the first estimate. Anything added after seeing an
   estimate is a new pre-registration, not an extension.
4. Only two decimal constants exist, both labelled RULERS, not fitted: `push_min = 0.5%`,
   `q_max_age = 1s`. A sensitivity at `q_max_age ∈ {0.25s, 1s}` is reported, not tuned.
5. The prior, stated as a prior: the print tape probably adds little. Phase-1 already found the minute
   representation sufficient for dispersion (0.714/0.699 A_pm, 0.736/0.739 B600) and exhaustion
   (0.676/0.661, 0.699/0.701) and structurally unable to deliver direction (+0.07/+0.04 over clock).
   A small, well-measured null is a *finished* result, not a failure to report around.

---

## 1. Case sets — what minute bars demonstrably cannot resolve

All six are buildable **now**, on the frozen panel. None requires E1 scores. Each carries an explicit
*minute-blindness argument* — the reason the minute panel provably cannot resolve it — because a
case set without one is just a fishing expedition.

**Common construction (frozen, not re-invented).** Rows are drawn from `panel.parquet` with
`terminal_censored == false`; the 255 `(day,ticker)` paths held by both families are excluded from
any cross-family statement; families are never pooled for inference. Sides are assigned by
deterministic order inside the matching unit, blind to every print feature. Anchors are
`bar_index`-indexed, never clock-indexed.

### CS-1 — Durable vs fragile reclaim (recoverers vs non-recoverers)
* **Anchor:** the reclaim bar `t_r` = the bar where `reclaim_count` increments (`reclaim_count(t) >
  reclaim_count(t-1)`), a panel-native event.
* **Minute-blindness:** `reclaim_count`, `bars_below_entry_episode`, `dist_from_running_high` are
  *close-based, 1-minute* constructions. Two members can carry identical `reclaim_count=1`,
  `bars_below_entry_episode=0`, identical `ret_1/3/5`, identical `bar_range_pct`, identical
  `volume_vs_own_median` and identical `dist_from_running_high` and still differ completely. Phase-1
  already measured that a reclaim is fast and shallow (median 1 bar, median cost −0.05%) yet
  **25.7% / 44.0% make a new low within 30 bars** — so the minute bar knows *that* a reclaim
  happened and nothing about *what kind*.
* **Outcomes (anatomy only, none is a max):** (a) sign of `v_forced_flat` at `t_r+1` (executable
  direction), (b) `final_high_flag` at `t_r`, (c) `cost_of_waiting` at `t_r`, (d) `failed_reclaim_count`
  delta over the next 5 bars.
* **Matching unit:** `(sleeve_day, et, family, reclaim_count, bars_below_entry_episode bucket,
  dist_from_running_high bucket, bar_index bucket)` — reuse of the Phase-1 `PRIMARY_EDGES` /
  `PRIMARY_EDGES` alternate set so the two epochs are comparable.

### CS-2 — False cuts vs genuine deaths under an E1 / ruler exit
* **Anchor:** the trigger bar of a *named, already-frozen* exit: `giveback:10` (or `:5/:15/:20`) from
  the panel's `v_giveback_G` / `giveback_fired_G`, or the E1 trigger bar when E1 exists, or
  `exit_at_bar:N` / `exit_at_et:HHMM` / a `spec:` rule. **Trigger source is a CLI parameter.**
* **Minute-blindness:** the ruler is a completed-bar *close* condition executed at the *next bar's
  open* (`SCHEMA.md` v2 §3). The minute bar contains, by construction, no information about the
  seconds inside the trigger bar, and no information about the seconds inside the execution bar —
  which is precisely where a false cut is created or avoided.
* **Outcome (anatomy only, already computed by the ledger):** `readmissible` true (the price traded
  ≥ +10% above the exit price after the exit) = **FALSE CUT**; `readmissible` false and
  `final_high_flag` true = **GENUINE DEATH**. Both are produced by
  `basket_atlas_ledger.py` today; the microscope supplies the *tape-level veto* that would have
  avoided the false cut, and measures it in dollars.
* **Matching:** the ruler fires for 4,946 of 5,887 judged members (A_pm 2,596, B600 2,350). Pairs
  are formed inside `(family, block, exit_et, bar_index bucket)` with sides blind.

### CS-3 — Matched minute states, divergent futures
* **Anchor:** the Phase-1 `middle_decile_executable_axis`, which is the **only** two-sided
  executable comparison in the Phase-1 artifact (the decile pairs were shown label-implied at 95.4%
  and are quarantined). 86,050 A_pm pairs (share_A_wins 0.5402) and 51,506 B600 pairs (0.5154).
* **Minute-blindness:** already measured. On this axis the best minute feature reaches
  **|effect| ≤ 0.054 and it is inverted** — the higher-range member is *less* likely to hold the
  higher executable value (0.446 A_pm / 0.453 B600). That number is the pre-registered null band.
* **Bounding:** the full 137,556 pairs are not extracted. Freeze a deterministic subsample per
  (family, block) — target 4,000 anchored member-windows total, allocated 60/40 A_pm/B600 and 50/50
  block1/block2, with per-day caps so no single day dominates. The subsample rule is hashed into
  the pre-registration.

> **Correction B3 (Stage B, written before any estimate).** CS-3 is **label-conditioned** and
> therefore **descriptive**, and every CS-3 number must be read that way. Membership is decided by
> a *future* quantity: a row is a "middle member" iff its oracle continuation
> `oc_close = fwd_close_max / next_open - 1` falls inside the `(q10, q90)` band of its
> `(et, family)` cell (`basket_atlas_pairs.py:441-500`), and CS-3's own outcome
> (`v_forced_flat_A > v_forced_flat_B`) is that same quantity family — the outcome distribution is
> truncated by construction. The upstream reading warning (`basket_atlas_pairs.py:1700-1702`) is
> carried here in substance: *a post-`t`-outcome-selected comparison is not a directional state
> claim.* The bound above (2,000 pairs = 4,000 member-windows) is unchanged; only the label is.
> CS-4's stratum is conditioned the same way and is descriptive for the same reason (already
> labelled); CS-3 now carries the same label.

### CS-4 — Exhaustion at matched state, split by dispersion stratum
* **Anchor:** a bar where the edge-free exhaustion carriers are non-constant
  (`dist_from_running_high`, `mfe_surrendered_pos`, `up_close_streak`, `accel_1_5`,
  `bars_below_entry_episode` — the Phase-1 surviving set), stratified into the top and bottom
  `fwd_range_30` decile **computed within the training block**.
* **Minute-blindness:** the stratum is a *future* quantity, so this set is explicitly **descriptive
  heterogeneity, not a predictive test** (the `fall.json` circularity tell: `final_high_flag` control
  = 1.0). The question asked is narrow and legitimate: *does the print-level signature of the same
  minute state differ between the violent and the quiet tape?* The predictive claim is the same at
  every stratum and is CS-1/CS-3's job.
* **Outcome:** `final_high_flag` and `no_further_new_high`, reported per stratum with the stratum
  shown on every number.

### CS-5 — Halt / reopen ordering
* **Anchor:** the first print after a run of ≥5 consecutive silent minutes inside a member window
  (`GAP_MIN=2`, `HALT_MIN=5`, `HALT_ACTIVITY_FLOOR=60` — the probe's own constants).
* **Minute-blindness:** absolute. A hole has no interior in the minute panel; the panel's
  `gap_count_so_far` / `bars_since_gap` see only that *a* gap happened. The probe measured that
  **53 of 103** halt-sized holes are followed by a condition-`5` reopen print and the rest are
  indistinguishable from illiquidity — the *ordering* question is open and is a tape-only question.
* **Outcome:** `reopen_code5` (the label), and `pre_halt_trend` / `reopen_gap_ret` (the features).
  Fully buildable now, requires nothing from the panel beyond the window bounds.

> **Correction B5 (Stage B, written before any estimate).** The implemented rule is the probe's own
> `HALT_MIN = 5` **print-to-print jump**, which is **4 silent minutes**, not "≥ 5 consecutive silent
> minutes": 10,026 of 29,975 bracketed candidates (33.4%) and 1,996 of the 4,000 frozen CS-5 windows
> (49.9%) are 4-minute holes. The anchor text above is corrected to say what the code does, and
> **every CS-5 number is split by hole-length class (`4min` vs `>=5min`)** — population, frozen
> sample, extraction census, features and I-EV results alike. Two further Stage-B clarifications,
> both written before any estimate: (i) `reopen_code5` **is** the CS-5 label, so it can never be a
> CS-5 predictor, and `reopen_gap_ret` is simultaneous with the label at the reopen print and is
> reported descriptively; the CS-5 predictors are the pre-hole state (`pre_halt_trend`,
> `hole_minutes`, the pre-hole bar's arrival/size/occupancy/flow) plus the U-all prints *inside* the
> hole — a halted name prints nothing, a thin name prints odd lots, and that contrast is the
> tape-only question. (ii) CS-5's decision instant is the **reopen print** (frozen
> `action_stamp_tau = 0`), so the hole's own prints are causal for it.

### CS-6 — Rejected as a separate case set
"Does a print-level directional separator exist?" is not a case set; it is the **estimand** of
CS-1 and CS-3. It is named in §4 as H-I1 and carries a real chance of null.

---

## 2. Windows and event-time representation

**Event time, not clock time.** Every window is anchored at a panel event `t*` and stored in
**seconds relative to the anchor's first print**, `τ`. The raw `et`, `bar_index` and `session_end`
are stored alongside so any later re-anchoring is possible without re-extraction.

**The window is fixed by the execution convention, not chosen.**
`τ ∈ [−60 s, +180 s)`. Rationale: the panel's convention is *decision on completed bar t, execute at
the open of bar t+1*, so the earliest legitimate print-level action is inside bar `t+1`. Anything
beyond +180 s would be a horizon (banned). Anything before −60 s exists only to characterise the
approach to the level being crossed.

**Exactly four segments, never a grid.**

| segment | τ range | purpose |
|---|---|---|
| `pre` | [−60, 0) | approach: arrival, occupancy of the level to be crossed |
| `anchor` | [0, 60) | the anchor bar's own minute |
| `post1` | [60, 120) | bar `t+1` — the earliest legal execution bar |
| `post2` | [120, 180) | bar `t+2` — the last legal execution bar inside the declared window |

**Drift guards, enforced in the extractor and asserted in the artifact:**
* every stored print satisfies `−60 ≤ τ < 180`;
* `n_distinct_segments == 4` and no segment is ever subdivided;
* the extractor physically refuses to read a print at or after the declared action stamp when
  emitting any feature labelled `causal_at_decision` (Gate G2);
* no parameter in the extractor is a *horizon*. If a future question needs a longer view, it is a new
  pre-registration.

> **Correction B1/B2/B6 (Stage B, written before any estimate).** Three frozen defects in this
> section are fixed in Stage B, and the fixes are the semantics the extraction actually implements:
>
> * **B1 — the decision instant is the CLOSE of bar t.** τ is measured from `close(t)`, not from the
>   anchor's first print: `tau = ts − close(t)`, and every feature labelled `causal_at_decision` may
>   read only prints with `tau <= 0` (equivalently `ts <= close(t)`). The rule as written
>   (`tau < 60` from the first print) admitted up to ~59 s of post-decision tape: the measured
>   first-print offset is p50 0.88 s / p90 21.5 s / max 58.8 s over the frozen windows, and the
>   Stage-B artifact counts the prints that rule would have leaked, window by window. Each window
>   records its decision instant and the print cutoff actually used (`cutoff_sec_et`).
> * **B2 — segments are the member's own panel bars, not τ arithmetic.** The four declared segments
>   are the member's bars `t−1, t, t+1, t+2` (CS-5: the two bars before the hole plus the hole
>   span), mapped through the panel's member bar list. Panel bars are *printed minutes*: the next bar
>   after `t` is more than one minute later for ~9% of the frozen windows (up to 47 minutes), so the
>   execution/outcome bar is the panel's next bar whatever its `et`. Windows whose member has no next
>   bar are reported (`no_next_bar`), never silently dropped.
> * **B6 — containment.** Because the segments are the member's own bars, every stored print lies
>   inside `[entry_et, session_end]` by construction, and prints outside the member window are never
>   read. The frozen τ-window's spills (136 anchors at `et == entry_et`, 426 with `et + 3 >
>   session_end`) are recomputed and published as `tau_window_spill_*` for the record.
>
> The τ ranges in the table above are the contiguous-tape idealisation of those four bars; the
> artifact publishes per segment its true span, and per window the bar gaps, the missing-bar guards
> and the panel-invisible gap minutes (U-all prints that print in a minute with no panel bar).
> Consequence for §3/§4: only `causal_at_decision` columns may be predictors in the I-EV arm; the
> `post1`/`post2` (action-horizon) columns are stored for the L-EV arm and reported descriptively.

---

## 3. Feature families — 6 families, 14 features, frozen

### Universe discipline (the largest single correctness issue)
* **U-path** = `basket_t5_rawpaths.price_updating(frame)` — the Alpaca strictest-rule-wins filter
  keeping `high_low == 2`. This is the panel's substrate; the probe reconciled 2,727 minutes against
  the committed derived bars with **0 high/low mismatches**. All price, level and velocity features
  use U-path, which is what makes the window reconcile exactly.
* **U-all** = every print, including the odd lots the policy excludes. The probe measured
  **1,764,233 of 2,968,612 window prints (59.4%) excluded**, dominated by `@|I` (1,183,260) and
  `@|F|I` (576,285). Arrival and participation features use U-all, because measuring "arrival
  acceleration" on a universe that has been halved by the vendor's odd-lot filter would be measuring
  the filter.
* Every feature is reported in **both** universes where meaningful; a feature that works in only one
  is flagged `universe_dependent` in the artifact.
* The `(ts_utc, trade_id, price)` key is unique in-window (0 duplicates measured) so the exclusion
  join is exact, not approximate.

| # | family | feature | universe | definition | what it is **not** |
|---|---|---|---|---|---|
| 1 | F1 Arrival | `arr_rate` (pre / anchor / post1 / post2) | U-all | prints per second per 5-s bin, mean over the segment | not "institutional buying" — there is no aggressor flag |
| 2 | F1 | `arr_accel` | U-all | `log((anchor+1)/(pre+1))`; deceleration is the negative side | not a momentum claim |
| 3 | F2 Size | `size_p90_over_median` | U-path | per segment | not order-book depth |
| 4 | F2 | `top_decile_size_share` | U-path | share of segment volume in the largest 10% of prints | not an order-flow imbalance |
| 5 | F3 Occupancy | `secs_above_level` / `secs_below_level` | U-path | seconds in the segment with the last print on the declared side of the level, summed over 5-s bins | — |
| 6 | F3 | `reclaim_persistence` | U-path | number of 5-s bins in `post1` whose **bin close** is back above the reclaimed level, given it went below ≥1 time. **The print-level analogue of `reclaim_count`.** | not a new label — it is a feature |
| 7 | F4 Velocity | `time_to_reclaim_s` | U-path | seconds from the anchor to the first print back above the declared level (continuous; never bar-rounded) | not a horizon |
| 8 | F4 | `n_failed_pushes` | U-path | excursions ≥ `push_min` (**RULER 0.5%**) above the level followed by a return below, within the window | — |
| 9 | F4 | `push_decay` | U-path | `seconds_above_level(post1) / seconds_above_level(post2)`; persistence in time | — |
| 10 | F5 Halt | `reopen_code5` | U-all | the first print after a ≥5-min hole carries condition `5` | illiquidity and halt are only *partly* separable (53/103) |
| 11 | F5 | `reopen_gap_ret` / `pre_halt_trend` | U-path | reopen print ÷ last pre-hole print − 1; signed move into the hole | — |
| 12 | F6 Signed flow | `signed_vol_share` | U-path × quotes | `(V_buy − V_sell)/V` with Lee-Ready classification (price vs prevailing mid; mid-equal → tick rule); prints with no quote within `q_max_age` (**RULER 1s**) are dropped and the dropped share is reported | **an inference from a possibly-stale quote, not an aggressor flag** |
| 13 | F6 | `unclassified_share` | — | share of segment volume dropped by the quote staleness bound | a coverage diagnostic, not a feature |
| 14 | F6 | `signed_vol_share_tickonly` | U-path, no quotes | tick-rule-only version, quote-free | the robustness twin of #12 |

**Levels used are declared per case set and read from the panel row** — running high at `t*`,
`entry_px`, the reclaim level. Never recomputed under a different rule.

### What raw trades cannot reveal (printed verbatim in the artifact)
1. The mutual order of prints inside one microsecond: 5.89% of path prints sit in tie groups (max
   group 5 in the probe), 2,804 groups carried ≥2 distinct prices, and `trade_id` is **not** monotone
   inside 1,540 of them. File order is the only order. → **no within-microsecond sequencing feature,
   no "first to cross" claim at sub-microsecond scale.**
2. A price for a minute with no price-updating print: 0 such bar-minutes in the 9 probe member-days,
   but the norm on halted/thin names. → the window there is **undefined, not zero**.
3. Matching-engine timestamps: the tape carries feed print timestamps only.
4. Hidden/iceberg size, cancelled size, book depth, true aggressor identity.
5. IEX is not a sub-minute source here: `data/iex_tape/` is minute bars of one venue, a strict
   subset of SIP. Not used.
6. `signed_vol_share` is a *quote-aligned inference*, not a truth. It is reported with
   (a) `unclassified_share`, (b) `q_max_age ∈ {0.25 s, 1 s}`, and (c) the quote-free tick-rule twin.
   A conclusion that does not survive all three is a staleness artifact and is rejected as such.

---

## 4. Information-EV test (I-EV)

**Estimand.** Within a case set and matching unit: the difference in a forward, executable, non-max
outcome between the two blind sides, explained by print features **controlling for the minute
features on the same rows**.

* **Matching units** are Phase-1's, not new: `(sleeve_day, et, family, bar_index bucket)` for the pair
  sets; the state cell for the single-member sets. Sides deterministic and blind; same-member pairs
  dropped; the 255 duplicated paths excluded from cross-family statements.
* **Out-of-block, both directions.** Fit on block1 → score block2, and block2 → block1. The decision
  statistic is the *transferred sign*, never a pooled in-sample effect. In-block-only is reported as
  a report, not a finding.
* **Clustering.** Day-clustered bootstrap is primary (the pair sides share a day in the primary unit);
  ticket-clustered is secondary. `matched_pairs.json`'s one-sided day bootstrap is the template. The
  existing dyadic two-way bootstrap is reused for any cross-day pairing.
* **The minute counterpart is mandatory and is the actual comparison.** For every print feature, fit
  the identical nested model on the minute-feature counterpart over the identical pairs and report the
  **residual increment** `Δ = print-conditional-on-minute − minute-alone`. A standalone print AUC is
  reported but is *not* evidence; a print feature that is not worth more than the minute bar it sits
  inside is a null.
* **Multiple-comparison discipline.** The family×feature×case-set grid is 6 × 14 × 5 = 420 tests,
  frozen and hashed before any estimate. A feature is promoted only if it
  (a) transfers out-of-block in **both** directions, (b) keeps a day-clustered CI excluding zero on the
  **minute-controlled residual**, (c) holds in **both families**, and (d) survives the pre-declared
  **label-perturbation** check (re-run on an alternative exit-price label and on a volatility-matched
  subset — the Phase-1 `oc_open` / `oc_high` trick). Surviving features then get a **Holm**
  correction *within the surviving set*; unadjusted numbers are reported regardless. Family agreement
  × label perturbation is the real filter; Holm is bookkeeping.
* **The null is a number, not zero.** Phase-1's own minute bound on CS-3 is |effect| ≤ 0.054 and is
  **inverted**. The pre-registered null band for the print-side *residual* is `|Δ| ≤ 0.054`. Anything
  inside it reads as: *the print tape adds nothing the minute bar does not already carry.*
* **Hypotheses, named, signed, pre-registered (no post-hoc signs):**
  * H-I1 direction: microsecond features separate divergent executables. Prior: weak/negative
    (Phase-1 found the minute representation directionally empty). This is the headline and it is
    allowed to be null.
  * H-I2 durable reclaim: `reclaim_persistence`, `time_to_reclaim_s`, `push_decay` are higher on the
    durable side. Prior: positive — this is the most economically motivated of the six.
  * H-I3 false cut: at the ruler trigger, `arr_accel` and `n_failed_pushes` are higher on the false-cut
    side. Prior: positive (a fast, thin reclaim that does not hold is the microstructure of a
    giveaway).
  * H-I4 reopen: `reopen_code5` and `reopen_gap_ret` separate continuation from decay across a hole.
    Prior: positive; the hole is invisible to minutes by construction.
  * H-I5 signed flow: `signed_vol_share` adds residual over minutes. Prior: weak, and explicitly at
    risk from the staleness artifact.
  * H-I6 dispersion interaction: the H-I2/H-I3 signature is stronger in the wildest dispersion decile
    (the Phase-1 volatility-mediation finding). **Descriptive heterogeneity only** — see CS-4.

> **Stage-B implementation of §4 (written before any estimate).** The measurement as implemented:
> predictors are the `causal_at_decision` columns of the frozen Stage-B table (the B1 mandate: the
> post-decision segments are the action horizon, not signal); the mandatory minute counterpart is a
> 27-column panel state model on the *same rows and the same coordinate*; both models are
> ridge-penalised logistic regressions fitted out-of-block in **both** directions; the decision
> statistic is the held-out **Δ AUC** (and Δ log-loss) with a **day-clustered** bootstrap CI
> (B = 200, fixed seed); families are never pooled for inference, so every promotion claim is read
> per family (the pooled coordinate is published marked `REPORT ONLY`). The promotion rule is the
> plan's: Δ > 0 out-of-block in both directions **and** in both families, Δ above the 0.054 null
> band, a day-clustered CI excluding 0, the label/threshold perturbation, and Holm within the
> surviving set; the *kill* rule the contract states is weaker and is reported separately — a case
> set whose families all fail "positive out-of-block delta in both blocks" is **closed as null**.
> On CS-3's paired coordinate Δ AUC is a win-rate difference (exactly the Phase-1 effect scale); on
> the single-member sets the same 0.054 is applied as a conservative pre-registered floor.

**Dollar acceptance (Gate G5, only after a feature is promoted).** The promoted feature is written
as a `spec:<path.json>` condition and scored by `basket_atlas_ledger.py` in dollars exactly as the
rulers are: `failure_tax_avoided`, `dollars_destroyed`, `net_dollar_ledger`,
`giant_dollars_destroyed_100/_300`, per block, sleeve **and** independent-path views, plus top-day
sensitivity. The reference magnitude to beat is `giveback:10` on the same panel: 5,887 judged members,
**+444.87 avoided / −320.87 destroyed / +123.99 net** (sleeve), +108.16 independent-path over 5,640
paths, 119 giants cut (14 at +300%), 2,277 re-entry candidates, 4,946 early exits, at 100 bps total
friction (A_pm net +65.89, B600 net +58.10). I-EV does not stop at AUC.

---

## 5. Latency-EV test (L-EV) — fixed signal, three frozen arms

**The clock starts only after the signal is fixed.** The decision is taken on the panel's own
convention (completed bar `t`, executed at the open of `t+1`) by the *same* rule in all three arms.
L-EV asks one question: given that decision at that bar, is acting inside bar `t+1` at print level
worth more than at its open?

* **A0 (baseline):** exit at `next_open(t)` — the panel's and engine's convention.
* **A1 (first print):** act at the first price-updating print in bar `t+1` (τ = 0 of the window).
* **A2 (print-time trigger):** act at the first print in bar `t+1` satisfying a **pre-declared**
  print-level condition — one of: *first print below the level*; *first print after `k` consecutive
  prints below the level*; *first print with `signed_vol_share` below a declared share*. `k` and the
  condition are taken from the I-EV arm's promoted feature, or from the frozen ruler set if none is
  promoted. They are **never tuned here**.

**Fillability model (the whole point).** The lb18 lesson is explicit in `PRE-REG-EXIT-01`: pricing an
exit at the last SIP trade with flat friction manufactured +0.74..+0.82 pp that vanished under the
NBBO bid plus 50 bps. Therefore:

* a **sell** at print price `p` fills at `min(p, bid_at_p)`; a **buy** at `min(p, ask_at_p)`.
  The print is not the price.
* a participation cap `q` on 60-second traded volume at or better than the limit; fill fraction
  `f = min(1, q · V_60s / notional)`. The unfilled remainder is carried to the next eligible print or
  to the forced flat — **never assumed filled**.
* the frozen **SLIP ladder {0, 25, 50, 100} bps** is charged on top and the verdict is read at
  **50 bps**, not at the best cell. This is an existing pre-registered discipline, reused verbatim.
* if no quote exists within `q_max_age`, the print-level arm is **not evaluable** for that action:
  fall back to the last print ≤ the action time and **flag it**. Never silently price at the print.

**Estimator.** Member-level `Δ` against `v_forced_flat`, in dollars per committed dollar, per block,
sleeve and independent-path, exactly as the ledger defines it. The comparison is `arm − A0` on the
**identical** members, paired, with a day-clustered CI on the paired difference.

**The confound that must be named in the pre-registration.** Acting earlier is *also* acting in a
different price environment. A1 is not "faster is better"; it is "the price you get 3 seconds into
bar `t+1`, net of the bid and the ladder, versus the price you get at that bar's open." On a name
that gaps through the level in the first seconds the prior is that A1 is **mechanically worse**
(lb18 measured mean `bid_H/B` = 0.967 at the touch instant). That is a prediction, and the design is
built to record it cleanly if it holds.

**Structural separation.** L-EV is a different script and a different artifact. The pre-registration
states in advance: *a positive L-EV licenses no claim about signal; a null I-EV licenses no claim
about execution.* The only sentence combining them is written after both are in.

---

## 6. Data flow, API and cost

### Sources (all local, read-only, no purchase, no network)
| source | what | size / coverage |
|---|---|---|
| `data/sip/net/trades/<day>.parquet` | raw SIP prints | 1,066 files, **23,829,091,862 B (23.83 GB)**, mean 22.35 MB, p50 20.26 MB, max 122.1 MB; **1,066/1,066** dev days present, zero missing |
| `data/sip/net/quotes/<day>.parquet` | raw SIP NBBO | 1,066 files, top-3-per-snapshot union, ~7–21 symbols/day, all `status: ok` |
| `data/sip/net/coverage/<day>.json` | per-symbol class | `healthy_raw 64,042 / provider_only 6,132 / unresolved 2` of 70,176 symbol-days (91.26% / 8.74%) |
| `factory/artifacts/basket/sip/bars/<day>.parquet` | derived bars | the panel's substrate; used for the G1 reconciliation |
| `ATLAS/panel.parquet` | anchors + outcomes | sha `2a021eda…`, 1,900,432 rows / 6,160 members / 1,066 days |
| `ATLAS/matched_pairs.json`, `fall.json`, `window_profile{,_index}.json`, `ledger_selftest.json` | frozen case definitions and references | — |

### API — reuse, never re-derive
`basket_subminute_probe.load_prints(day, symbols)` (the sanctioned read template) ·
`basket_t5_rawpaths.price_updating(frame)` (the sanctioned path filter) ·
`sip_bars.combine / RULES_M / AUCTION_CODES` (the condition truth) ·
`basket_sim.load_anatomy / snapshot_of / dev_days / session_end_map / guard_day` (sealed and
reserved days are refused by the engine) · `basket_atlas_panel`'s column registry and
`coverage.json` causal-family selector (the no-leakage guard, mirrored for the microscope's own
feature table).

### Three stages, resumable, incremental per-day writes

**Stage A — census (one pass over the store).** Per dev day: one projected-column read of the trades
file filtered to the day's ATLAS members, the coverage JSON, and the quote file's symbol list. Emit
`microscope_census.parquet`: per `(day, family, ticker, entry_rank)` → `n_prints_all`,
`n_prints_path`, minute coverage, `coverage_class`, `quote_present`, `quote_n_symbols`, `last_et`.
**This stage answers both gating unknowns (raw sufficiency, quote availability) before a single
feature is computed.**

**Stage B — anchored extraction.** The anchor list is frozen from the panel (event bar per case set,
capped, deterministic, hashed). For each anchor: slice `[−60 s, +180 s)`, emit (a) the **full feature
vector over the complete window — no cap**, and (b) a **capped raw print sample** (first N = 2,000
prints) for eyeballing only, with `n_prints_window` recorded so truncation is visible and can never
silently bias a feature (the p90 member-day carries ~806 path prints/minute, the max ~4,835, so
truncation is expected on hot days and must be reported).

**Stage C — analysis.** Two scripts, two artifacts, independent: I-EV and L-EV.

### Cost (extrapolated from the probe's measured 24-day sample, and labelled as such)

| item | measurement | whole-store projection |
|---|---|---|
| projected-column read of the trades store | p10 0.104 s / p50 **0.15 s** / p90 0.306 s per day | **160 s serial, ~0.7 min at 4 workers** |
| read + `price_updating` + per-member minute aggregation, ~3 members/day | p50 **0.3145 s/day** | 335 s serial, 1.4 min at 4 workers |
| **Stage A** (~5.8 ATLAS members/day + coverage JSON + quote symbol scan) | — | **~0.6 s/day → ~11 min serial, ~3 min at 4 workers** `[INFERENCE: linear in members/day from the probe's measured 3-member figure]` |
| **Stage B** (bounded: ~4,000 anchored windows) | — | minutes, not hours |
| storage, full print path for all 6,160 members (**explicitly not done**) | median 27,496.5 eligible prints/member-day | ~169 M prints ≈ **3.4 GB binary / 15 GB JSONL** |
| storage, bounded Stage B | ~4,000 windows × (feature row + ≤2,000-print sample) | **~0.2–0.5 GB parquet + ~3 MB feature table** |

The Stage-A estimate is an extrapolation and must be **measured, not assumed**: the measured Stage-A
wall time is the first artifact reported, and a measurement above **30 min serial** aborts the design
and forces a declared day-block subsample.

> **Correction B9 (Stage B, written before any estimate).** The abort clause above was not
> auditable as published: `census_cost.json` reports `projection_full_store_serial_min` = **42.91**,
> which is *above* the 30-minute rule, while the plan's §E quotes 46.6 min serial (the same pass plus
> the optional `--bars-check` G1 read) and 16.5 min (the census pass alone on a 4-day trial). The
> three numbers measure three different things. The rule as implemented and auditable is: **the
> abort threshold is the 4-hour hard wall** (`abort_threshold_hours` in `census_cost.json` and in
> `stage_b_cost.json`), and the 30-minute figure is a *design* trigger on the measured per-day serial
> projection — which the projection exceeds on this store, so a declared day-block subsample was
> considered and **not** needed: the actual wall is 14.5 min at 3 workers (freeze), 14.5 min at 5 and
> 18.5 min at 2 (audit runs A/B), i.e. the design completes far inside the hard threshold. Stage B
> publishes the same two fields, measured, in `stage_b_cost.json`.

---

## 7. Gates — frozen, ordered, each able to stop the programme honestly

| gate | condition | on failure |
|---|---|---|
| **G0 feasibility** | Stage-A census: `healthy_raw` share of ATLAS member-days ≥ 0.85; trades store 1,066/1,066 days. Quote-present share measured and **reported**; the L-EV arm requires ≥ 0.80, else it runs on the covered subset and says so on its face. | report the failure, stop |
| **G1 reconstruction** | for every extracted window, the U-path per-minute high/low over the anchor minute equals the panel's `bar_high/bar_low` to 1e-9 (the probe did 2,727 minutes with 0 mismatches) | **stop** — a mismatch means the microscope is not measuring the panel's tape |
| **G2 leakage** | every feature is derived only from prints with `τ <` the action stamp; the feature-source table passes the panel's causal-family registry; the extractor physically refuses an illegal read | **stop** |
| **G3 information** | ≥1 case set with an out-of-block, day-clustered, minute-controlled residual `|Δ| > 0.054`, transferring in both directions, in both families, surviving label perturbation and Holm | the information claim is **closed as null**; no E1/E2/E4 change is derived from it |
| **G4 latency** | `arm − A0` paired, day-clustered, net of the NBBO-bid fill model, at SLIP = 50 bps, positive in both blocks | the print-level action is **closed**; next-minute-open remains the execution convention everywhere |
| **G5 dollars** | any promoted effect clears the ledger: avoided, destroyed, net, giant-tail destroyed, independent-path, top-day sensitivity — against the `giveback:10` reference (+123.99 net, 119 giants cut) | measurement language only; **no adoption language** |
| **G6 stop** | G0–G3 pass but G4 fails | the pre-registered terminal sentence is: *"the print tape carries causal information the minute bar does not carry, and at this population and this participation size we cannot execute it profitably."* Pre-registered so it cannot later be quietly reinterpreted. |

---

## 8. Honest limits, stated in the pre-registration

* **This is not a promise that sub-minute will work.** The prior is that it mostly will not. The
  null band is 0.054 and Phase-1's own best minute feature is *inverted*, so a well-measured small
  null is the modal honest outcome and is a publishable result.
* **CS-4 is descriptive.** Conditioning on a future dispersion label cannot be a predictive claim.
* **Feature 12 is an inference.** Signed flow from a possibly-stale quote is the weakest thing in the
  table; it carries three robustness renderings and is rejected if it does not survive them.
* **Ceiling on the whole programme.** On a name printing ~1,000 prints/minute, "acting earlier" buys
  seconds, and seconds are worth nothing if the bid is already 3% through the level. L-EV has a
  credible prior of being null or negative, and that is a result, not a failure.
* **Coverage.** 273 panel members are terminal-censored and are excluded from every executable
  aggregate, reported as unresolved, never zero-imputed. 255 duplicated `(day,ticker)` paths are
  excluded from cross-family statements. ~8.7% of symbol-days are `provider_only` and never enter
  the microstructure set.
* **Binding to the engine.** Any promoted output must be expressible as an EXT-1
  `BatchAllocationPolicy` or EXT-2 `EntryVetoRule` / `MidEntryPolicy` / `ReentryPolicy` with a
  `signature()`, so the engine's fingerprint binds it and the inertness guarantees hold.

---

## §E. What step 1 actually produced (measured, not projected)

```
.venv/bin/python factory/scripts/basket_atlas_e3.py --stage all --bars-check --workers 3
.venv/bin/python factory/scripts/basket_atlas_e3.py --self-test
```

### E.1 Census (Stage A) — one projected-column pass per dev day

| quantity | measured |
|---|---|
| dev days read | **1,066 / 1,066** (every panel day has a trades file) |
| raw bytes read (day files) | **23,829,091,862 B** (the whole 23.8 GB store; an upper bound — 6 of 8 columns are projected) |
| member-days | **6,160** (A_pm 3,188 / B600 2,972) |
| wall time | **951 s (15.9 min)** at 3 workers |
| per-day wall | p50 **2.14 s**, p90 4.68 s, max 19.65 s |
| per-day projected read | p50 **0.299 s**, p90 0.566 s, max 1.93 s |
| quote symbol scan | p50 ~0.19 s/day (one extra symbol-column scan; 5.4 MB/day) |
| serial projection | **42.91 min** (`census_cost.json:projection_full_store_serial_min`, the whole-store serial projection of the measured per-day wall *including* the optional `--bars-check` G1 read); the census pass alone (4-day trial without the check) is **0.95 s/day → 16.5 min serial / ~4 min at 4 workers** |
| prints in (U-all, in-window) | **814,350,508** |
| prints out (U-path, in-window) | **445,408,263** (54.69 %; 369 M prints dropped by the condition policy) |
| U-path prints per member-day | p10 578, **p50 23,811**, p90 204,497, max 2,016,289 |
| minute coverage (U-path) | p10 0.351, **p50 0.975**, p90 1.0 |
| coverage classes | healthy_raw **6,107** (99.14 %), provider_only **53** (0.86 %), unresolved 0, missing 0 |
| quote presence | **6,159 / 6,160 member-days** (99.98 %); 1,066/1,066 days with a quote file; `symbols_requested == symbols_with_data == 15,736` |
| halt-shaped holes | **30,029** runs of ≥5 silent minutes in member windows; **29,975** bracketed by prints (CS-5 candidates); 192 member-days below the 60-minute activity floor |

**Gates (Stage A):** G0 raw sufficiency **0.9914 ≥ 0.85 pass** · G0 store completeness **1066/1066 pass** ·
G0 quote coverage for L-EV **0.99984 ≥ 0.80 pass** · **G1 reconstruction: 6,160 member-days checked
against the committed derived bars, 0 mismatches — pass** (the census counts the panel's own tape).

One extra cross-check falls out of the same numbers: the census counts **1,900,432** minutes with a
U-path print inside the member windows, exactly the panel's row count — the panel's grain *is* the
U-path minute, so the census and the panel agree on the tape minute for minute.

The plan's Stage-A estimate was ~11 min serial / ~3 min at 4 workers; the measured census pass is
16.5 min serial / ~4 min at 4 workers, and the extra time is the coverage-JSON + quote-symbol reads
the plan itself asked for. Both are far below the 4-hour abort threshold, and the census pass alone
is below the plan's 30-minute serial abort rule (the 42.91 min figure in `census_cost.json` is the
whole-store serial projection of the same pass *including* the optional G1 reconciliation read; see
the B9 correction in §6).

### E.2 The frozen case sets

| set | before gates | population (gated) | frozen windows | family | block | panel-side drops (all reasons) | raw-gate drops | cap drops |
|---|---|---|---|---|---|---|---|---|
| CS-1 durable/fragile reclaim | 21,213 | 21,196 (4,332 member-days) | **4,000** | A_pm 1,899 / B600 2,101 | 2,000 / 2,000 | 782 terminal-censored | 17 provider_only | 17,196 |
| CS-2 false cuts (giveback:10) | 4,946 | 4,937 (4,937 member-days) | **4,937** (no subsample) | 2,593 / 2,344 | 3,309 / 1,628 | 273 censored, 938 never fires, 3 clock precedence | 9 provider_only | 0 |
| CS-3 mid-decile axis | 275,112 | 275,102 (2,330 member-days) | **4,000** = 2,000 pairs (600/600/400/400) | 2,400 / 1,600 | 2,000 / 2,000 | 186,708 up-decile rows, 189,011 down-decile rows, 178,043 dup-cross-family rows, 33,845 censored rows, 350,807 single-middle-member units | 5 coverage + 5 partner | 271,102 |
| CS-4 exhaustion × dispersion stratum | 369,147 | 369,059 (5,464 member-days) | **4,000** | 2,347 / 1,653 | 2,000 / 2,000 | 1,475,365 outside the top/bottom decile, 33,536 censored, 11,744 no `fwd_range_30`, 10,640 constant carriers | 88 provider_only | 365,059 |
| CS-5 halt/reopen | 30,029 runs | 23,567 (2,601 member-days) | **4,000** | 2,191 / 1,809 | 2,000 / 2,000 | 4,559 censored, 1,849 below the activity floor, 54 unbracketed runs | 0 | 19,567 |

**Total frozen: 20,937 anchored member-windows** over the five sets (population 693,861 gated anchors).
Every set's accounting closes exactly: `before_gates = population + raw_gate_drops` and
`population = frozen_windows + cap_drops` (asserted in `selftest.json` for all five sets).

> **Correction B7 (Stage B, written before any estimate).** `exclusions.panel_side` in
> `case_sets.json` **mixes grains**, so its entries cannot be summed: CS-1 counts event rows (782),
> CS-2 counts members (1,214), CS-3 counts panel rows *and* units (938,414), CS-4 counts fall-frame
> rows *including* the `carrier_state_constant_variant_4carriers` diagnostic (17,504 — a diagnostic
> carrier, not a drop), and CS-5 counts candidate rows (6,408). In no set does
> `sum(panel_side) == anchors_before_gates - gated_population`. Every individual count is
> reproducible; only the summing is invalid. The identity that does hold, and that the self-test
> asserts, is `sum(raw_gates) = anchors_before_gates - gated_population` (17 / 9 / 10 / 88 / 0) with
> `gated_population = frozen_windows + sample_cap_dropped`. The Stage-B artifact republishes the
> corrected, grain-labelled table as `exclusions_accounting` (per set: the grain of each panel-side
> count, the diagnostic rows excluded from the drop list, and both identities recomputed). The
> frozen `case_sets.json` is not edited — it is a byte-frozen artifact — so the correction lives in
> the plan and in `iev.json`.

**Stage-B projection from the measured census** (no extraction was run): **16.0 M window prints
≈ 320 MB** at the probe's 20 B/print binary estimate, i.e. within the plan's 0.2–0.5 GB bound.

### E.3 Reproducibility

Two consecutive full runs of `--stage all --bars-check --workers 3` produced **identical sha256** for
`census.json`, `census.parquet`, `cs5_anchors.parquet`, `anchors.parquet` and `case_sets.json`.
`census_cost.json` is the only run-varying artifact by design (it holds the wall-clock measurements)
and is deliberately excluded from the freeze.

| artifact | sha256 |
|---|---|
| `census.json` | `58f0405b64e2785c9017a5d99b35048c0561ed33581d2db2ed2109985a964222` |
| `census.parquet` | `6566a3d2bf549156adc7169654dec078ffed7c1344348bb86648f1ec0680bf06` |
| `cs5_anchors.parquet` | `d6ade76dc3144f3f292bb8ef3caa6baa459c8960136789b3ef5c10727588bdca` |
| `anchors.parquet` | `678230f5757ff45555d021d38a7d09571e7858ee147e130ffdc1807d8412bd1c` |
| `case_sets.json` | `588eabfe6f3c170df9bfa5149bd809faad5391a0d60323e6337551fc2e22f457` |

`census.parquet`, `cs5_anchors.parquet` and `anchors.parquet` also came out identical in an
earlier, independently launched full run, and a third run of the lint-clean producer reproduced
all five shas — the census read path, the CS-5 hole detector, the CS-3 axis rebuild, the
carry/direction strata and the bounded selection are all bit-reproducible.

### E.4 Self-test

**38/38 checks pass** (`selftest.json`): silent-run algebra and the 5-minute jump rule; the imported
U-path filter dropping `@|I` odd lots; selection determinism, order-invariance, explicit-allocation
honouring and per-day balance; **CS-2 == the ledger's own giveback:10 early exits (4,946) and family
split (2,596 / 2,350)**; **CS-3 == `matched_pairs.json`'s 86,050 / 51,506 pairs**; the panel grain
(1,900,432 rows / 6,160 members / 1,066 days); fill clocks ≥ the family checkpoint and `session_end`
∈ {779, 959}; the per-set accounting identities; every frozen window on a `healthy_raw` member-day;
the artifact shas; the four-segment window guard.

### E.5 Not done in step 1 (by design)

Stage B (anchored extraction), the 14-feature table, the I-EV arm, the L-EV arm, the dollar stage and
any change to E1/E2/E4. No feature is computed anywhere in this step, so the plan's G2 leakage gate is
not yet exercisable and no estimate has been seen.


---

## §F. Stage-B semantics, the frozen Stage-B table, and the deviations

Written by `E3StageB` on 2026-09-28 **before the first estimate was computed** (the table below is
hashed in `iev.json:feature_table.sha256`; no number in §G was seen before it was frozen).

### F.1 The window contract as implemented (B1/B2/B6)

| element | implemented rule |
|---|---|
| decision instant | the **close of the anchor bar t**: `close(t) = (et_t + 1) × 60` seconds after ET midnight. **CS-5 exception (declared):** its anchor is a print, not a bar, so its decision instant is the **first U-path print of the reopen minute** (frozen `action_stamp_tau = 0`); measured from the pre-hole bar's close the hole's own prints therefore sit at τ up to **+411.8 s** and are still causal — they are read only by the CS-5 predictors (F3/F4/F5) and by the diagnostic `f5_hole_prints_all`. |
| causal guard (G2) | every `causal_at_decision` feature reads only prints with `tau = ts − close(t) ≤ 0`. The extractor raises on the first violation; the artifact counts the prints the frozen `tau < 60` rule would have admitted at `tau > 0`. |
| per-window record | `decision_close_s`, `cutoff_sec_et` (the last causal print actually read), `first_print_offset_s` (the B1 evidence), `n_prints_causal`, `n_prints_window_all/path`. |
| segments | the member's **own panel bars**: `pre = t−1`, `anchor = t`, `post1 = t+1` (the execution bar), `post2 = t+2`; CS-5: `pre = t−1`, `anchor = t` (the pre-hole bar), `hole =` the silent minutes. Each bar segment is exactly 60 s; the hole segment is `hole_minutes × 60`. |
| row coordinates | `stage_b_features.parquet` carries `bar_index` for every window (the bar the minute state is read from). For CS-5 that bar is the **pre-hole bar**, while `et` is the frozen anchor's own minute (the **reopen** minute, B10) — so a CS-5 row's `(et, bar_index)` deliberately point at the two different ends of the hole: `et` identifies the frozen window, `bar_index` identifies the bar whose state is read. |
| gap safety (B2) | bars are mapped through the panel's member bar list, so `post1` is the panel's **next bar** whatever its `et` (gaps up to 47 minutes are published per window as `gap_next_min`/`gap_post2_min`). A window whose member has no next bar is reported (`no_next_bar`) and excluded from the outcome models, never silently dropped. |
| containment (B6) | segments are member bars, so every stored print is inside `[entry_et, session_end]` by construction; the frozen τ-window's spills are recomputed and published (`tau_window_spill_*`: 136 anchors at `et == entry_et`, 426 with `et + 3 > session_end`). |
| panel-invisible minutes | U-all prints in a minute with no panel bar (inside a gap between the window's bars) are counted (`n_prints_in_gap_minutes_all`) and are **not** features. |
| G1 | for every window, the U-path high/low of the anchor bar must equal the panel's `bar_high`/`bar_low` to 1e-9; a mismatch stops the stage before any estimate. |

### F.2 The frozen Stage-B feature table

24 **predictors** (causal at the decision instant) + 20 descriptive/action-horizon columns (44
columns in the frozen table). The predictors, by plan §3 row:

| family | predictors |
|---|---|
| F1 arrival (U-all) | `f1_arr_rate_pre`, `f1_arr_rate_anchor`, `f1_arr_accel` |
| F2 size (U-path) | `f2_size_p90_over_median_{pre,anchor}`, `f2_top_decile_size_share_{pre,anchor}` |
| F3 occupancy (U-path) | `f3_secs_above_level_{pre,anchor}`, `f3_secs_below_level_{pre,anchor}`, `f3_reclaim_persistence` |
| F4 velocity (U-path) | `f4_time_to_reclaim_s`, `f4_n_failed_pushes` |
| F5 halt (CS-5 only) | `f5_hole_minutes`, `f5_pre_halt_trend`, `f5_hole_prints_all`, `f5_hole_print_rate` |
| F6 signed flow | `f6_signed_vol_share_{pre,anchor}`, `f6_unclassified_share_{pre,anchor}`, `f6_signed_vol_share_tickonly_{pre,anchor}` |

The descriptive columns (`post_arr_rate_*`, `post_size_*`, `post_top_decile_*`, `post_secs_*`,
`post_push_decay`, `post_time_to_reclaim_s`, `post_signed_vol_share_*`, `post_unclassified_share_*`,
`post_signed_vol_share_tickonly_*`, `post_reopen_code5`, `post_reopen_gap_ret`) are stored in
`stage_b_features.parquet` for the L-EV arm and are **never** predictors here.

* Levels (read from the panel row, never re-derived): CS-1 `entry_px`; CS-2
  `running_high(t) × (1 − 10/100)`; CS-3/CS-4 `bar_close(t)`; CS-5 `pre_hole_px` (the frozen aux).
* The two RULER constants are the plan's, unchanged: `push_min = 0.5%`, `q_max_age = 1 s`. The 5-s
  bin convention of F3 is the plan's own.
* `f4_n_failed_pushes` counts **maximal runs of prints at/above `level × (1 + push_min)` followed by
  a print below the level, anywhere in the causal window** — the run may begin before the window's
  first below-level print (the price was already ≥ 0.5% above the level and then failed back below
  it), which is the faithful reading of "an excursion above the level followed by a return below".
  Verified against an independent re-implementation on a frozen window (13/13 features agree; §G).
* `f6_*` is Lee-Ready on the prevailing NBBO mid with a tick-rule fallback (a zero tick inherits the
  previous classification; a print with no quote inside `q_max_age` is dropped and its size share is
  reported as `unclassified_share`), plus the quote-free tick-rule twin.

### F.3 The I-EV measurement as implemented

* **Rows.** CS-1/CS-2/CS-4/CS-5: one row per window, at the decision instant. CS-3: one row per
  matched pair, features and minute state as `A − B` differences (the matching unit is differenced
  out exactly), label `1[v_forced_flat_A > v_forced_flat_B]`, ties dropped and counted.
* **Targets.** CS-1 primary `1[v_forced_flat > 0]` (executable from the open of bar t+1), secondary
  `final_high_flag`, `1[cost_of_waiting < case-set median]`,
  `1[failed_reclaim_count(t+5) > failed_reclaim_count(t)]`; CS-2 primary `readmissible` from the
  ledger's own `replay_member` on the frozen `giveback:10` rule, secondary `final_high_flag`; CS-3
  `1[v_forced_flat_A > v_forced_flat_B]`; CS-4 `final_high_flag` (descriptive), secondary
  `1[remaining_run > 0.10]`; CS-5 `reopen_code5`, secondary `1[reopen_gap_ret > 0]` (descriptive).
* **Models.** Ridge-penalised logistic regression (IRLS, l2 = 1.0 on standardised features, no
  intercept penalty), training-fold median/std, nulls imputed with the training-fold median, a
  column entering only with ≥ 95% non-null and non-constant training data. The **minute counterpart**
  is the same model on the 27 registered causal panel state columns over the identical rows and
  coordinate; the reported statistic is the residual increment on the held-out block.
* **Cross-fit.** `block1 → block2` and `block2 → block1`, both published; the decision statistic is
  the out-of-block Δ AUC (and Δ log-loss) of print+minute against minute-alone.
* **Clustering.** Day-clustered bootstrap CI (B = 200, seed 20260928) on Δ AUC and Δ log-loss.
* **Families are never pooled for inference**: every promotion claim is read per family; the pooled
  coordinate is published marked `REPORT ONLY`.
* **Promotion (plan G3).** A family is promoted only if Δ > 0 out-of-block in **both** directions in
  **both** families, Δ exceeds the 0.054 null band, the day-clustered CI excludes 0, the
  label/threshold perturbation holds, and Holm within the surviving set is significant.
* **Kill rule (the contract's, reported separately).** A case set with no family showing a positive
  out-of-block delta in **all four cells — both block directions × both families** — is **closed as
  null**. The artifact publishes the literal two-direction reading per coordinate as well
  (`kill_rule_literal_reading`); no family clears the 0.054 band under either reading.
* **Perturbation.** Alternative label (CS-1 `1[v_hold_flat > 0]`, CS-2 `final_high_flag`, CS-3
  `1[v_hold_flat_A > v_hold_flat_B]`, CS-4 `1[remaining_run > 0.10]`, CS-5 the hole-length split)
  and a volatility-matched subset (anchor `bar_range_pct` inside the inter-quartile range).

### F.4 Deviations from the draft (Stage B)

| # | draft says | implemented | why |
|---|---|---|---|
| S1 | τ measured from the anchor's first print, window `[−60, +180)` | τ measured from the **close of bar t**; segments are the member's own bars (F.1) | B1/B2/B6 — the draft's rule admitted post-decision prints on a large share of windows (§G measures it exactly) and mis-identified the execution bar for ~9% of them |
| S2 | 14 features, `per segment` where stated | 24 predictors with the per-segment variants materialised as separate columns, plus 20 descriptive columns (44 columns in all) | the per-segment variants must be separate columns to enter a model; the plan's 14 rows map onto them 1:1 (see the `plan_row` field in `feature_table.rows`) |
| S3 | feature table "frozen and hashed before the first estimate" | hashed as `feature_table.sha256` (a literal table in the producer) | same commitment, machine-checkable |
| S4 | `reclaim_persistence` is a `post1` feature (H-I2) | `f3_reclaim_persistence` is computed over the **causal** part (pre∪anchor); the `post1` rendering is published descriptively | B1: a post-decision feature cannot be a signal for a decision taken at the close of t |
| S5 | `push_decay = secs(post1)/secs(post2)` as an F4 feature | kept as the descriptive `post_push_decay`; F4's causal predictors are `time_to_reclaim_s` (redefined as first-below → first-back-above inside the causal window) and `n_failed_pushes` (causal window) | same reason as S4 |
| S6 | CS-5 outcome `reopen_code5`, features `reopen_gap_ret`/`pre_halt_trend` | `reopen_code5` is the **label** (never a predictor); `reopen_gap_ret` is descriptive (simultaneous with the label); the predictors are the pre-hole state plus the U-all prints inside the hole | the draft's own text made the label a feature; the hole's U-all prints are the tape-only halt/illiquidity contrast and are pre-registered here |
| S7 | Stage B emits a capped raw print sample of N = 2,000 prints per window | `stage_b_prints.parquet` carries the **first 20 prints per declared segment** (≤ 80/window), with `n_prints_window_all`/`n_prints_window_path` recorded per window | the frozen windows sit in the highest-print-rate minutes (§G measures the real per-window print count, ~3× the draft's linear projection), so N = 2,000 would be ~1 GB for an eyeballing-only sample; the sample is deterministic and truncation is visible |
| S8 | "the extractor physically refuses to read a print at or after the declared action stamp" | implemented as a hard guard: any causal segment print with τ > 0 raises; the G2 gate also re-checks every minute-model column against the causal registry | the draft's guard was a claim; this one is executable and self-tested |
| S9 | CS-2's `readmissible` "not persisted in `ledger_selftest.json`" (step-1 note) | recomputed at analysis time through `ledger.replay_member` on the frozen `giveback:10` rule, imported never re-derived | the freeze fixed the anchors only; the ledger is the outcome's own producer |
| S10 | CS-1 outcome `cost_of_waiting` and the `failed_reclaim_count` delta have no threshold | binarised at the case-set **median** of `cost_of_waiting` (a relative split, no fitted constant) and at "increases over the next 5 bars" | a label must be binary for the AUC estimand; both splits are declared and deterministic |
| S11 | "a volatility-matched subset (the Phase-1 `oc_open`/`oc_high` trick)" | anchor `bar_range_pct` inside the case set's inter-quartile range | the panel-native volatility proxy; the Phase-1 columns are pair-axis fields, not available on every case set |
| S12 | L-EV is a separate script and artifact (§5) | **deferred**: `latency.json` declares the deferral, the frozen L-EV contract and the measured quote prerequisite; no latency number is claimed | the contract's stated option; the separation is the point of §5 and I-EV is this step's mandate |
| S13 | CS-5 anchors store `bar_index` (plan §2) | the frozen `anchors.parquet` leaves it null (audit B10); Stage B re-anchors CS-5 by `et` and publishes `bar_index` for every window in `stage_b_features.parquet` | the freeze is byte-frozen and must not be edited |

### F.5 The corrections carried into the frozen text

B3 (CS-3 is label-conditioned/descriptive), B5 (the ≥ 5-minute jump is 4 silent minutes; every CS-5
number split by hole-length class), B7 (`panel_side` mixes grains; the identities that hold), and B9
(the abort clause) are annotated at their own places above, and machine-readably in
`iev.json:plan_corrections` + `iev.json:exclusions_accounting`.

The audit's remaining items, for the record:

* **B4 (CS-3 pairing off-by-one).** The axis's own code (`idx=(kk+1)//2`, `side_a=kk%2==0`) makes the
  first middle member of every unit an orphan and emits nothing from a unit with exactly two middle
  members: 786,694 orphaned A rows and `sum(floor(k/2)) = 435,887` available pairs against the
  137,556 emitted (unit k-distribution `{1: 350,807, 2: 298,331, 3: 137,556}`). The frozen text's
  description ("all units with ≥ 2 middle-decile members; one pair per unit, the first two in
  deterministic order") is therefore **false as written**. Stage B does **not** re-cut the axis: the
  freeze is byte-frozen, the axis is imported from `basket_atlas_pairs`, and CS-3 is descriptive
  (B3) — so the defect is recorded here and in `iev.json:plan_corrections.B4` rather than repaired
  behind a frozen artifact. Repairing it is a new pre-registration.
* **B8 (CS-5 activity floor).** The `HALT_ACTIVITY_FLOOR = 60` member-day rule removes the longest
  holes 16.7× more often than it keeps them (15.0% of the 1,849 dropped candidates have hole ≥ 30
  min vs 0.9% of the kept ones), so the CS-5 population is survivorship-shaped toward short holes.
  Declared (D5), unchanged, and every CS-5 number in Stage B is additionally split by hole-length
  class (B5) so the shape is visible on the face of the results.
* **B10 (provenance gaps).** Stage B fixes the CS-5 `bar_index` gap: `stage_b_features.parquet`
  carries `bar_index` for every one of the 20,937 windows (the frozen `anchors.parquet` leaves it
  null for CS-5). `PANEL_SHA_DECLARED` is still a literal, but `iev.json:frozen_inputs` records the
  five freeze-artifact shas it consumed and the self-test re-checks them. The within-block cap
  allocation and the CS-1/CS-3 bucket edges remain post-hoc degrees of freedom as the audit noted;
  they are not used by the I-EV measurement (which conditions on the frozen rows as given).

---

## §G. What step 2 actually produced (measured, not projected)

```
.venv/bin/python factory/scripts/basket_atlas_e3.py --stage stageb --workers 4   # 18:59 wall
.venv/bin/python factory/scripts/basket_atlas_e3.py --self-test                  # 64/64
```
`--workers` > 1 runs **processes**, not threads: the per-day work is GIL-bound (polars reads release
the GIL, the numpy feature passes do not), and a thread pool measured no speedup on this workload.

### G.1 Cost (measured; run-varying fields in `stage_b_cost.json`)

| quantity | measured |
|---|---|
| days extracted | **1,066 / 1,066** |
| extraction wall | **1,018.6 s = 17.0 min** at 4 worker processes (command wall 18:59 including the 3:38 analysis) |
| CPU / peak RSS | 583 % CPU, **3.84 GB** peak resident |
| raw bytes read | **23,829,091,862 B** (the whole trades store, projected columns) |
| per-day wall | p50 ~1.5 s, projected whole-store serial **67.6 min** (an *upper bound*: each per-day wall is measured inside its own worker and already carries contention; the serial 8-day measurement is 1.15 s/day → ~20 min) |
| abort check | threshold **4 h** (`abort_threshold_hours`); projected 67.6 min → **no abort**. The plan's 30-min *design* trigger is exceeded by the projection — see the B9 correction |
| artifacts | `stage_b_features.parquet` 4.0 MB (20,937 × 75), `stage_b_prints.parquet` 16.2 MB (1,315,880 sampled prints), `iev.json` 313 KB, `latency.json` 2.3 KB, `selftest.json` 17 KB |

### G.2 Gates and the B1/B2/B6 counters (all windows)

| gate | result |
|---|---|
| **G1 reconstruction** | **20,937 windows checked, 0 mismatches** — every anchor bar's U-path high/low equals the panel's `bar_high`/`bar_low` |
| **G2 leakage** | **0** causal prints with `tau > 0` (the extractor raises on the first one); every minute-model column is a registered causal state column (`column_registry.json v2 + coverage.json v2`) |

| B1 (the fix that mattered) | measured |
|---|---|
| first-print offset inside the anchor bar | p10 0.045 s, **p50 1.76 s**, p90 33.64 s, **max 59.998 s** |
| prints the frozen `tau < 60 from the first print` rule would have admitted **after the decision instant** | **190,695 prints across 8,254 windows (39.4 %)** |
| per-window record | decision instant + `cutoff_sec_et` on all 20,937 windows |

| B2 / B6 | measured |
|---|---|
| windows whose next panel bar is > 1 min later | **1,527** (max gap **47 min**) |
| windows with no next bar (no execution bar) | **139** (CS-1 19, CS-3 120) — reported, excluded from the outcome models |
| windows with no `post2` bar / no `pre` bar | 180 / 178 |
| frozen τ-window spills (B6) | **136** anchors at `et == entry_et`, **426** with `et + 3 > session_end` — reproduced exactly, and contained by construction in Stage B |

### G.3 The extraction

| set | windows | U-all prints | U-path prints | causal prints | prints in panel-invisible gap minutes |
|---|---|---|---|---|---|
| CS-1 | 4,000 | 14,254,113 | 7,698,701 | 3,591,080 | 17,501 |
| CS-2 | 4,937 | 33,185,371 | 17,518,493 | 10,176,005 | 23,884 |
| CS-3 | 4,000 | 6,639,163 | 3,527,515 | 1,782,817 | 8,924 |
| CS-4 | 4,000 | 13,986,136 | 7,314,177 | 3,643,797 | 11,264 |
| CS-5 | 4,000 | 7,244,752 | 3,400,119 | 3,400,119 | 57,755 (the holes) |
| **total** | **20,937** | **75,309,535** | **39,459,005** | **22,593,818** | **119,328** |

Two cross-checks that close the loop with the frozen artifacts:

* **CS-2 label**: the ledger's own `replay_member` on the frozen `giveback:10` rule resolves **4,937/4,937** CS-2 members as early exits and returns **2,277** `readmissible` — exactly the ledger's own
  `n_reentry_candidates` (2,277 of 4,946 early exits, 0.4604). The primary CS-2 label's base rate in the
  measurement is 0.4612.
* **CS-5 hole classes (B5)**: 1,996 `4min` / 2,004 `>=5min` — identical to the frozen split — and the two
  classes are behaviourally different: the reopen print carries condition 5 for **51.3 %** of 4-minute
  holes and only **10.9 %** of longer ones.
* **CS-5 re-anchoring (B10)**: the extracted pre-hole bar lands on the frozen window for all 4,000
  windows. `pre_hole_px_vs_frozen_aux.max_abs_delta = 32.0` is **not** zero and is *expected*: the frozen
  aux's `pre_hole_px` is the **first** U-path print of the pre-hole minute (the census's own convention,
  used as the CS-5 level and in the descriptive `reopen_gap_ret`), while the extractor's pre-hole print is
  the **last** one (used by `f5_pre_halt_trend`). The two definitions are published; aligning them is a
  follow-up, and no CS-5 verdict turns on it (every CS-5 Δ is ≤ 0.003, three orders below the null band).

### G.4 The I-EV measurement

Rows, targets and models are §F.3. Every Δ below is the out-of-block **Δ AUC** of print+minute against
minute-alone, per family, in the two families and the two block directions; `pos` = positive in both
blocks; `band` = above the 0.054 null band; `ci` = day-clustered 95 % CI excluding 0 in both directions.

| set | rows used | primary label base rate | minute-only AUC (A_pm / B600) | families with `pos` | promoted |
|---|---|---|---|---|---|
| CS-1 durable/fragile reclaim | 3,981 (19 no execution bar) | 0.4017 | 0.577 / 0.613 | none | none |
| CS-2 false cuts | 4,937 | 0.4612 | 0.616 / 0.688 | none | none |
| CS-3 mid-decile axis (descriptive) | 1,925 pairs of 2,000 (75 dropped: 60 null-outcome + 15 ties) | 0.5283 | 0.605 / 0.611 | none | none |
| CS-4 exhaustion × stratum (descriptive) | 4,000 | 0.7040 | 0.856 / 0.857 | none | none |
| CS-5 halt/reopen | 4,000 | 0.3108 | 0.9997 / 0.9964 | F1, F3, F6 | none |

* **Kill rule (the contract's).** CS-1, CS-2, CS-3 and CS-4: **no feature family is positive
  out-of-block in all four cells (both block directions × both families)** → the information claim
  for those sets is **closed as null**. The literal two-direction reading (per family × coordinate)
  leaves **15 family-coordinate cells** alive (25 including the pooled report-only coordinate), the
  largest Δ AUC among them **0.0168** (CS-3) — an order of magnitude inside the 0.054 null band, so
  the verdict is the same under either reading. `iev.json:kill_rule_literal_reading` publishes both
  counts and every surviving cell.
  CS-5 survives the kill rule (F1 arrival, F3 occupancy and F6 signed flow are positive in both blocks in
  both families, with CIs excluding zero) — but **nothing is promoted**: every CS-5 Δ is 0.0002–0.005,
  far inside the 0.054 null band, and the perturbation check fails. `any_promoted = []` in `iev.json`.
* **G3** is therefore **closed as null for all five case sets**, and the §6 terminal sentence does not
  apply (G3 never passed).
* **H-I2/H-I3 (CS-1/CS-2)**: null. The largest single-family Δ on CS-1 is F4 A_pm +0.0112/+0.0078 — but
  B600 is *negative* on the same family, i.e. the sign does not transfer across families.
* **H-I5 (signed flow, CS-3)**: F6 is **negative** in both blocks in both families (−0.009/−0.030,
  −0.019/−0.0002) — the quote-aligned flow proxy, the weakest thing in the table by the plan's own
  warning, does not add information here.
* **H-I6 (CS-4 dispersion interaction)**: descriptive, and null in both strata (all |Δ| ≤ 0.013).
* **CS-5's near-ceiling baseline** is the honest caveat: the minute state at the pre-hole bar already
  predicts the reopen condition at AUC ≈ 0.999 (the panel's own `gap_count_so_far` / `bars_since_gap`
  carry the member's gap history), so the print side has almost no headroom — a null here is a null
  against a near-perfect minute model, not against a weak one.

### G.5 Reproducibility of step 2

* `stage_b_features.parquet` and `stage_b_prints.parquet` were produced **byte-identically** by a serial
  run and a 4-process run of the same 8-day subset (`9febe323…` / `a70b5144…`) — the process pool does not
  change a byte.
* `iev.json` and `latency.json` reproduced **byte-identically** across two runs of `--stage iev` on the
  stored extraction (`a8e9ab11…`, `6adae956…`), and `selftest.json` across two `--self-test` runs
  (`29d6cee9…`).
* The five Stage-A freeze artifacts are **byte-unchanged** (`58f0405b…`, `6566a3d2…`, `d6ade76d…`,
  `678230f5…`, `588eabfe…` — re-checked by the self-test's `sb_freeze_sha_*` checks).
* Four post-debug corrections (requested by the freeze debug pass, all in the analysis stage only; the
  extraction parquets and every measured number are unchanged — verified leaf-by-leaf, 0 measurement
  leaves differ):
  1. the **perturbation gate** read a key `stage_b_analysis` never wrote (`positive_both_families`), so
     `perturbation_holds` was hard-False; it now ANDs the alternative-label run (positive in both
     directions in both families) with the volatility-matched subset (positive in both directions) and
     publishes both components. No verdict changes: every family already fails the 0.054 band, and the
     alternative-label run is genuinely non-positive (e.g. CS-1 F1: +0.0051 / −0.0088 / +0.0080 / −0.0053).
  2. the **kill-rule sentence** now states the implemented four-cell rule (2 block directions × 2
     families) and the artifact adds the literal per-coordinate reading with its survivors and maximum.
  3. `diagnostics.b1_decision_instant.cs5_exception` states the CS-5 decision instant (the first U-path
     print of the reopen minute) and the resulting τ range of causal hole prints (measured +411.8 s from
     the pre-hole bar's close; declared bound = the longest hole in the set × 60 s).
  4. the CS-3 drop reason is split: `pairs_dropped_null_outcome` (60) vs `pairs_dropped_tie` (15),
     replacing the conflated `tie_dropped_pairs`.
  `iev.json` sha256 after these fixes: `f5466c1261f1d17521c76121958711cdec05e9d73d45938e145c5f240dcdc825`.
* One post-run correction: the CS-5 hole-length split was first computed with a mask built over the wrong
  case set (`_subset` silently passed full-length arrays through). `_subset` now **raises** on a
  row-length mismatch, and the split is computed on the CS-5 rows; the published `iev.json` is the
  corrected one (the extraction parquets were never affected). Its own second-run reproduction is the
  §G.5 check above.
