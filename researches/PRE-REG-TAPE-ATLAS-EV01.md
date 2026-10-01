# PRE-REG — TAPE-ATLAS EV-01 (minute-horizon descriptor/outcome read)

**STATUS: FROZEN 2026-10-01 — NOTHING EXECUTED.** No outcome column has been read. Every decision marked **FROZEN** is closed and is no longer a degree of freedom; the file's freeze precedes execution. Doctrine inherited: ET clocks, causal-only inputs, 1-bar lag, next-bar-open fills, ≥100 bps friction, blocked = cash, pre-registered kills (`AGENTS.md`; `factory/BASKET-SIM-CONTRACT.md`, FROZEN-2026-09-22 + SUBSTRATE-CORRECTION-2026-09-24). Economics boundary per `researches/PLAN-TAPE-ATLAS.md` §9 / §10 Stage 5 (exits/execution only after geometry freeze) — this read is that step.

## 0. Cited artifacts (shas measured on these files; `<DATA>` = `data/atlas`)

| artifact | sha256 | identity |
|---|---|---|
| `…/ATLAS/panel.parquet` | `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488` | 1,900,432 rows / 6,160 members / 5,905 paths / 1,066 d / 88 cols; `PANEL_SHA_EXPECTED` in the ledger |
| `<DATA>/geometry/v1/objects.parquet` | `6c0f655ba76cdd176f8a7857df4895e25aeb5c711cd8d1294a4564954e26f30f` | 17,715 objects = 5,905 paths × scales {30,60,120}; all `retrospective_only`, `live=false` |
| `<DATA>/geometry/v1/rare_refs.parquet` | `e7ae275c74576d653c1195eeb1efd04d5d154cb062aa8e1274e6410ad42f7423` | 8,577 rare objects = 2,859 paths × 3 |
| `<DATA>/observation/v0/memberships.parquet` | `d461c926321ac5730e01570a0eee16ba90fb4baa7d367b72ea9ac020fb1563d5` | 6,160 member rows |
| `…/TAPE/GEOMETRY/v1/retrieval_report.json` | `5673570180dc327024d96975c880be681d2dcf3ec5b39866ab4616f87425f47e` | node `geometry.tape_atlas.hand_views`, `report_version=freeze-r.geometry.v0` |
| `…/TAPE/representation_matrix_v1.json` | `9720d0ef27ce7272b52c7cc30522d4271b776e6fe30783bfb93a78c8ffb8a2d9` | frozen Freeze-R record the geometry read |
| `<DATA>/sequence/v1/runs/{causal,retrospective}/block/embeddings.npz` | `e1cacdd613c429cd59b359607e09e30dd8486774792057d7ef08af931e359c6b` / `1a67fc8cd352139919f07af8e4f3892c14110716efd61f3e40a49830156ddf18` | learned view, `(values,valid)=(17715,32,120)` — **not** in the geometry retrieval |
| `…/TAPE/SEQUENCE/v1/sequence_reports_by_mode.json` | `1e30bab430bf75e008421b4531cca330d73a8fd9cd0f7279092ff142cb2151f1` | learned-view provenance and one-shot gates |

Ledger surface: `MEMBER_KEYS=("sleeve_day","family","entry_rank","ticker")`, `fric_k(bps_total)`, `run_ledger(df, rule, bps_total=100.0)`, DSL `hold_flat | exit_at_bar:N | exit_at_et:HHMM | giveback:G`.

## 1. QUESTION

Does the evidenced within-name structure separate forward member returns from the hold-flat baseline at minute horizons, net of 100 bps?

## 2. UNIT AND JOIN

- **Unit** = one member = `MEMBER_KEYS` = `(sleeve_day, family, entry_rank, ticker)`, `family ∈ {A_pm, B600}`.
- **Membership identity (verified 1:1).** `memberships.parquet` = exactly 6,160 rows, unique `member_id = "<day>|<family>|<ticker>|<entry_et>|<entry_rank>"` and `path_id = "<day>|<ticker>"`, one `panel_sha256 = 2a021eda…` on all rows. Its counts (block1 4,218 / block2 1,942; A_pm 3,188 / B600 2,972) equal the panel census (`coverage.json.per_block`: block1 734 d / 4,218; block2 332 d / 1,942) and the panel README. This is the join key set; every member is in the panel.
- **Member → object(s).** `path_id` joins a member to that tape's geometry objects: exactly 3 per path, `scale ∈ {30,60,120}`, `object_id = "<path_id>@<scale>"`, `anchor_et = first_entry_et`, left-aligned. Object path set == membership path set exactly (5,905 = 5,905, verified by set identity); 255 paths carry two members (the shared A_pm/B600 `(day,ticker)`; 5,905 + 255 = 6,160).
- **Triple correlation.** The three objects of a path are one tape at three resolutions with overlapping source intervals — **not** three independent observations. Each descriptor is computed per object and **aggregated to the member as the unweighted mean over the member's three scale objects** (FROZEN — the three objects are correlated triples of one tape, so a member must contribute exactly one descriptor row).

## 3. OUTCOMES (fixed; no degrees of freedom)

- **Rule family** `exit_at_bar:N`, `N ∈ {1,5,30}`, vs the **`hold_flat` baseline rule**. `exit_at_bar:N` triggers on the completed bar with `bar_index == N` and executes at the **open of the next tracked bar** (`next_open`); a trigger at `et >= session_end-1` is preempted by the engine forced flat (`hold_equivalent`), and no later bar ⇒ `hold`. `hold_flat` = the ledger's hold rule = hold to the forced flat at `future_forced_flat_px`. **Naming hazard (explicit):** the executable baseline is the *forced-flat* hold, **not** the panel's `v_hold_flat` close column, which the ledger marks "diagnostic ONLY, never the executable baseline".
- **Friction.** `bps_total = 100.0` → `side = 0.005`, `k = fric_k(100.0) = (1-side)/(1+side) = 0.9900497512437811` (applied to both legs).
- **Outcome.** `Delta_member = k·(gross_rule − gross_hold)`, both gross from `entry_px` (`gross_rule = exit_px/entry_px − 1`, `gross_hold = future_forced_flat_px/entry_px − 1`). This is exactly the ledger's `delta` (`run_ledger` docstring: `delta = k*(gross_rule - gross_hold)`); `replay_member(sub, rule, k)` returns it per member. Read only through the ledger path — no re-derivation, no same-bar execution.
- **Blocked fills = cash** (`BASKET-SIM-CONTRACT.md` §4: cash, retained in the denominator, never backfilled). `panel.parquet` is the post-fill (filled, non-blocked) population, so blocked slots have no member row and no tape object here; they are reported as coverage only and **no `Delta` is fabricated for them**. FROZEN: the engine's `n_blocked_slots` census is reported beside the member ratio and is **never mixed into it**.
- **Censoring.** `terminal_censored` members have no terminal outcome: `Delta` **undefined**, never zero; excluded from the ratio and from every bootstrap draw, and counted/reported per block — 273 members / 33,845 rows (1.78%) total, block1 165 (3.91% of 4,218), block2 108 (5.56% of 1,942) (`coverage.json.censor_census`).

## 4. DESCRIPTORS (exactly three; frozen geometry, outcome-blind)

Computed **before** any outcome column is read; descriptor code may not import the outcome module or name any `outcome_*`/`v_*`/`future_*`/censor column.

- **(a) Neighbourhood density, balanced view.** Field `density` of `<DATA>/geometry/v1/view_balanced_multichannel_reference.nn.npz`, indexed by the member's path object row index in `objects.parquet`; definition (geometry code) = count of objects whose squared `standardized_view_distance` ≤ the frozen `radius = 83.03286591124858`, excluding self and every sibling scale of the same tape. Published cross-check: `rare_refs.density_balanced_multichannel_reference` for rare rows; `retrieval_report.views.balanced_multichannel_reference.by_block.median_density` = block1 100.0 / block2 47.0.
- **(b) Rarity flag.** `1` iff the object has `density == 0` in **all five** published views (magnitude, normalized_shape, duration_event, activity_microstructure, balanced) — no neighbour within that view's declared radius. Fields: the `density` array of each of the five `<DATA>/geometry/v1/view_*.nn.npz`. Published equivalent on the rare set: `rare_refs.views_unique` (comma-joined view names with density 0; 8,577 rows = 2,859 paths × 3); `retrieval_report.unique_and_uncertain_mass` per-view `n_unique_within_radius` (balanced 1,590; duration 6,843; activity 333; magnitude 429; normalized 0). Member-level = union over its three objects. FROZEN: exactly the `rare_refs` rule — no neighbour within the declared radius in **any** of the five views.
- **(c) Learned-view NN distance.** The frozen learned view is **not** in the v1 geometry retrieval (`retrieval_report.input_contract.learned_view = null`, `run_choices.embeddings = null`; only five hand views are published), so this descriptor is produced at execution from the frozen v1 embeddings: `values`/`valid` `(17715,32,120)` + `object_ids` in `<DATA>/sequence/v1/runs/causal/block/embeddings.npz`. Distance = the geometry script's frozen masked squared-L2 identity `D = q·m + m·q − 2u·u`, `inf` where no commonly valid position exists, with the frozen exclusion of self and overlapping-interval same-tape neighbours, `k=100`, `chunk_rows=512`; descriptor = smallest finite distance (equivalently `nn_dist[:,0]` / `kth_dist` from that pass). Provenance: causal mode, `prospective_live_use=true`, 30 epochs, weights sha `45b4ce03a583889b81151b0a64e1140adb6e16192f334b49682597c97fcd9c8c`, `ssl_over_random_improvement = 0.9415` ≥ gate 0.02. FROZEN: **causal mode only**; computed **inline with the frozen distance identity** — the geometry producer is **not** re-run. This descriptor step is a separate outcome-blind computation that publishes its **own artifact + sha256** and reads **no** outcome column.
- **Binning.** FROZEN: deciles of the member-level descriptor are computed **pooled over all members** (all 6,160, equal-count bins on descriptor rank, ties by `(descriptor, member_id)`), **block-tagged**, so each decile is a single pooled cut whose block membership is reported. The per-block decile cuts (block1 over block1 members, block2 over block2) are retained as a reported secondary view only and cannot replace the pooled cut.

## 5. STATISTIC AND CIs

- **S1** Spearman rank correlation between the member-level descriptor and `Delta_member`, per horizon.
- **S2** top-decile mean `Delta_member` − bottom-decile mean `Delta_member`, per horizon.
- **Interval**: **month-blocked bootstrap** — resample calendar months with replacement (all members of a drawn month move together), 10,000 draws, `seed = 20260922` (`BASKET-SIM-CONTRACT.md` §9), percentile intervals reported at 95% and at the frozen Bonferroni level (1 − 0.05/3 = 98.33%). Reported **per block** (block1 35 months / 734 d / 4,218 members; block2 16 months / 332 d / 1,942 members) and **pooled** (51 months). All three horizons, both statistics, every block cell. No censored member enters any draw.
- **Primary cells**: **all three horizons are primary.** FROZEN: Bonferroni α = 0.05/3 = 0.0167 is applied to each horizon's interval (per block and pooled separately), so a horizon is called only if its 98.33% month-blocked interval excludes 0.

## 6. KILL / DISMISS RULES (FROZEN 2026-10-01 by parent — K1–K7 as proposed)

- **K1 sign stability.** Pooled Spearman sign must equal the sign in block1 *and* block2; any disagreement → DISMISS.
- **K2 CI covers 0.** The Bonferroni-adjusted interval for S1 (α = 0.05/3 = 0.0167, applied to each primary horizon) includes 0 in the pooled cell → DISMISS that horizon.
- **K3 monotonicity.** In each block, decile-mean `Delta` must be monotone over top-5 vs bottom-5 deciles in the pooled sign's direction; failure in either block → DISMISS.
- **K4 effect floor.** |pooled Spearman| < **0.02**, or pooled top-vs-bottom decile `Delta` spread < **100 bps** (one round trip) → DISMISS as economically immaterial.
- **K5 power floor.** Any block×horizon cell with < **200** resolved members (non-censored, descriptor present) is REPORT-ONLY and cannot support a claim; pooled < **500** at a horizon → horizon dropped.
- **K6 empty bucket.** Any decile with < **30** members in a block voids K3 for that block (reported; deciles are not merged).
- **K7 coverage.** Censored share > **10%** in any block×horizon cell → DISMISS that cell. (Measured: 4.43% overall; block1 3.91%, block2 5.56% — inside the floor.)

## 7. EXCLUSIONS

- No sub-minute formulation here (separate pre-registration required).
- No next-session or carry horizons; the read ends at the member's own session.
- No post-hoc horizon (`N ∈ {1,5,30}` closed) and no post-hoc descriptor (a/b/c closed).
- No outcome read, no peek, before freeze; no descriptor may be re-defined after the first outcome read.
- No strategy language: this read measures separation only and declares no entry/exit/sizing rule.
- **Executed once per horizon family after this freeze; every result is published, nulls included** (no re-runs, no horizon re-selection, no silent retries). The sub-minute formulation is a **separate future pre-registration** and is not covered here.

## 8. EXECUTION PLAN

1. Freeze this file (parent commit); record its sha.
2. Ledger baseline + rules: `python factory/scripts/basket_atlas_ledger.py --rule hold_flat --bps 100 --json <out>/hold.json`, then the same for `exit_at_bar:1|5|30`; per-member rows via `members_of(df)` + `replay_member(sub, rule, k)` keeping `delta`, `terminal_censored`, `block`, `month`, keys. Expected ≤ 2 min, RSS < 2 GiB (1.9 M rows).
3. Descriptors (script imports no outcome module): load the five `view_*.nn.npz` `density` arrays and the causal embeddings archive, index by `objects.parquet` `object_id`, aggregate to member by unweighted mean over the three scales via the `path_id` join; publish the descriptor table as its **own artifact with its own sha256**, before any outcome column is touched. Expected ≤ 5 min, RSS < 3 GiB.
4. Learned-view NN pass (descriptor c), causal mode, inline frozen masked metric, `k=100`, `chunk_rows=512`; the geometry producer is not re-run. Minutes–tens of minutes, BLAS-bound (peak-RSS policy warn 15 GiB / abort 30 GiB, `PLAN-TAPE-ATLAS.md` §11). Run this step alone (no concurrent heavy job; the resident-set policy is measured, never assumed).
5. Join descriptors to `Delta_member`; pooled block-tagged deciles; compute S1/S2 and the month-blocked bootstrap (10 k, seed 20260922).
6. Publish under `factory/artifacts/basket/phase2/ATLAS/EV/v1/`: `member_deltas.parquet` (keys + `delta` per horizon + descriptor values + decile), `tables.json` (per-block and pooled S1/S2 with CIs, per horizon, with n and censored counts), `bootstrap_distributions.npz` (the 10 k draw arrays), `coverage.json` (censored/blocked/joined counts), `manifest.json` pinning every input sha in §0 plus the output shas, code sha and ledger/JSON shas. Incremental per-horizon writes; no `.tmp` left behind.
