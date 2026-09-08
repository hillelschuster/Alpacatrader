# Overlap & Redundancy Report — Gen 2 vs Gen 3

**Agent:** SUBAGENT D — Overlap & Redundancy Detector
**Date:** 2026-06-13
**Scope:** All reachable source in `src/`, tests, config, main.py
**Audit trail:** SPEC §22.16 (legacy purge) completed before this analysis.

---

## Executive Summary

Gen 2 (legacy) code has been **fully purged** from the runtime — no importable modules remain in `src/`. The SPEC §22.16 mandate was executed successfully. All 22 post-audit remediation items (§22.17) are checked off.

What remains is **Gen 3 (rebuild) code** with **5 internal architectural overlaps** that, if left unresolved, create maintenance drag, config fragmentation, and test duplication for v0.3.0.

**Canonical choice for v0.3.0:** Gen 3 rebuild. No Gen 2 code should be resurrected.

---

## Duplicate Systems Table

| System | Gen 2 Location | Gen 3 Location | Overlap Type | Status |
|---|---|---|---|---|
| **Pipeline / Strategy Flow** | `src/pipeline/v3_pipeline.py` (RossCameronPipeline) | `src/decision_pipeline.py` (`run_pipeline`) | Full replacement — same role, completely different philosophy | ✅ Purged Gen 2 |
| **Scanner / Discovery** | buried inside RossCameronPipeline | `src/scanner/scanner.py`, `src/scanner/enrichment.py` | Replacement | ✅ Purged Gen 2 |
| **Attention Scoring** | inside RossCameronPipeline | `src/scanner/attention.py` | Replacement | ✅ Purged Gen 2 |
| **Hard Filters** | legacy anti-patterns / regime / pillars | `src/hard_filters.py` | Replacement — mechanical-only | ✅ Purged Gen 2 |
| **Move Classifier** | none (old system used 5 Pillars) | `src/move_classifier.py` | New in Gen 3 | ✅ Gen 3 only |
| **Entry Detection** | none (old system had no defined-risk setup detectors) | `src/entries.py` | New in Gen 3 | ✅ Gen 3 only |
| **Exits** | inside RossCameronPipeline | `src/exits.py` | Replacement | ✅ Purged Gen 2 |
| **Execution** | `src/pipeline/v3_pipeline.py` (RossCameron) | `src/paper_execution.py` | Replacement — paper-first | ✅ Purged Gen 2 |
| **Sizing / Risk** | `src/risk/` (directory, multi-file) | `src/sizing.py` (single file) | Replacement — pure math | ✅ Purged Gen 2 |
| **State Machine** | none | `src/state_machine.py` | New in Gen 3 | ✅ Gen 3 only |
| **Data Models** | scattered in legacy `src/models/` | `src/models/schemas.py` | Consolidation | ✅ Gen 3 only |
| **Journal / Logging** | inside RossCameronPipeline | `src/journal/decision_logger.py` | Replacement | ✅ Gen 3 only |
| **Config / Settings** | `config/settings.py` (Phase1Settings) | `config/settings.py` (Phase1Settings — same file, evolved) | Same file, rewritten | ✅ Updated in-place |
| **Market Data** | scattered | `src/market_data.py` | New | ✅ Gen 3 only |

### Gen 3 → Gen 3 Internal Duplicates

| System | File A | File B | Overlap | Severity |
|---|---|---|---|---|
| Enrichment logic (VWAP, EMA, HOD, dollar volume) | `src/market_data.py:140-172` | `src/market_data_sim.py:106-125` | ~70% identical derived-enrichment code | **HIGH** |
| Pipeline wiring (gw, logger, runner store setup) | `main.py:_run_rebuild_mock:106-196` | `main.py:_run_rebuild_paper:199-287`, `main.py:_run_rebuild_sim:290-372`, `main.py:_run_paper_loop:375-409` | 4 nearly identical entry-point setups | **HIGH** |
| Time gate constants | `src/hard_filters.py:346-351` | `src/exits.py:363` (flatten_time default) | Split across 2 files | **MEDIUM** |
| Soft warning mapping | `src/scanner/attention.py:440-574` | — | Conceptually belongs to filter layer, not scanner | **LOW** (packaging) |
| `Bar` dataclass | `src/entries.py:42-63` | — | Used by 8+ modules but defined in a domain-specific file | **LOW** (packaging) |

---

## Canonical Choice and Rationale

**Gen 3 rebuild is canonical for v0.3.0.** No Gen 2 code should be used, referenced, or resurrected.

| Criterion | Gen 2 (Legacy) | Gen 3 (Rebuild) |
|---|---|---|
| Philosophy | Filter-first / Pillars / AI gates | Attention-first / definable risk |
| Architecture | Monolithic pipeline | Module-per-concern, composable |
| Testability | Network-dependent | Fully injectable callbacks |
| Data models | Scattered, no validation | Pydantic v2, validated |
| State mgmt | Ad-hoc | Explicit state machine |
| Exit engine | None (inline) | 11-priority emergency-first |
| Entry setups | None | 6 defined-risk detectors |
| Sizing | Complex risk module | Pure math, single file |
| Config | Cluttered | Minimal Pydantic settings |
| Runtime state | 989 tests passing | Same |

**Verdict:** Gen 3 is strictly superior on every dimension. v0.3.0 must remain Gen 3 exclusively.

---

## Merge Requirements

These are Gen 3 → Gen 3 internal merges needed before v0.3.0:

### M1 — `market_data.py` + `market_data_sim.py` → single enrichment source

- **Files:** `src/market_data.py:140-172` and `src/market_data_sim.py:106-125`
- **Problem:** Derived enrichment (VWAP `:152-155`, EMA9 `:158`, day_high `:162`, prior_hod `:165-167`, trailing dollar volume `:170-172`) is duplicated verbatim between live and sim paths.
- **Action:** Extract shared enrichment computation into a `compute_enrichment(bars) -> dict` helper imported by both market_data and market_data_sim. The dict keys: `vwap`, `ema9`, `day_high`, `prior_hod`, `dollar_volume_5m`.
- **Benefit:** Single source of truth for indicator math; sim mode can't diverge from live.

### M2 — `main.py` entry points → unified entry-point builder

- **Files:** `main.py:106-196` (mock), `main.py:199-287` (paper), `main.py:290-372` (sim), `main.py:375-409` (paper loop)
- **Problem:** Each mode constructs `AlpacaExecutionGateway`, `DecisionLogger`, and `FormerRunnerStore` identically, then calls `run_pipeline`/`run_pipeline_batch` with nearly identical kwargs. Sim mode even reconstructs `Candidate` objects from scratch (`:89-104`).
- **Action:** Create a `_build_pipeline_components(settings) -> dict` helper returning `{gw, logger, former_runners}`. Create a `_run_pipeline_batch(candidates, **shared_kwargs) -> list[PipelineResult]` used by all modes.
- **Benefit:** Adding a new mode (e.g. live) requires one entry point call, not 50 lines of duplicated wiring.

### M3 — Soft warnings → extract from `scanner/attention.py`

- **File:** `src/scanner/attention.py:440-574` (`map_soft_warnings()`), `:577-638` (`soft_warning_multiplier()`)
- **Problem:** 200 lines of soft-annotation logic (SPEC §8) lives in the attention-scoring module. It is imported by `decision_pipeline.py` but has nothing to do with scoring attention — it is a cross-cutting annotation layer used after attention but before hard filters.
- **Action:** Move `map_soft_warnings()`, `soft_warning_multiplier()`, and their constants to `src/hard_filters.py` (renamed to `src/filters.py` or extracted to `src/annotations.py`). Update imports in `decision_pipeline.py:35-40` and `decision_pipeline.py:265-270`.
- **Benefit:** Module-per-concern: attention.py scores attention, filters.py gate-keeps. Cleaner import graph.

---

## Deletion Candidates

### D1 — `market_data.py:147-172` (derived enrichment) — REPLACE, not delete

Replace with `compute_enrichment(bars)` helper so the inline calculation becomes a one-liner:
```python
enrichment = compute_enrichment(bars)  # replaces :147-172
```

### D2 — `market_data_sim.py:106-125` (derived enrichment) — DELETE lines

After M1, these lines become:
```python
enrichment = compute_enrichment(bars)  # replaces :106-125
```

### D3 — `move_classifier.py:367-378` (`setup_allowed()` / `get_allowed_setups()`) — KEEP

These functions are used by both tests and `decision_pipeline.py`. The §22.17.5 audit considered removing them. Keep as test-support API. No deletion needed.

### D4 — `state_machine.py:132-152` (candidate lifecycle helpers) — LOW VALUE

`CANDIDATE_LIFECYCLE`, `candidate_stage_index()`, `candidate_has_reached()` are dead code — nothing calls them. They were speculatively built for §13.1 but never wired into the pipeline.
- **Action:** Remove `:132-152`. If needed later, it's trivially recreated from the SPEC.
- **Lines to delete:** 20

### D5 — `entries.py:225` (`proposed_shares=1` placeholder) — ALREADY FIXED

The audit §22.5 noted placeholder sizing. The pipeline now overrides via `model_copy(update=...)` at `decision_pipeline.py:340-343`. The `_build_signal()` default of 1 share is harmless arm's-length design but could confuse readers.
- **Action:** Add a code comment at `entries.py:224` noting the placeholder is replaced by `decision_pipeline.py` sizing.
- **Actual code change:** Comment-only, no functional change.

---

## Spec Implications

### For `docs/SPEC.md` §16 (Module Map)

The spec's recommended module map at `SPEC.md:1493-1519` already matches Gen 3 closely. Two adjustments for v0.3.0:

1. **Add `market_data.py`** to the module map — currently missing from the SPEC file list.
2. **Rename `hard_filters.py` → `filters.py` (or document `annotations.py` split)** — SPEC §7 (Hard Filters) and SPEC §8 (Soft Annotations) are two different concerns but the implementation currently splits them across `hard_filters.py` and `scanner/attention.py`. The SPEC should clarify whether this split is intentional or if both should live in a single `filters.py`.

### For Phase 11 (v0.3.0 Config Surface)

The config surface (`SPEC.md:1530-1592`) still references `attention.` weights and `tradeability.` thresholds that are hardcoded in Gen 3. No code reads `phase1.max_candidates`, `data.quote_feed`, `attention.price_attention_weight`, or `tradeability.max_spread_pct_normal` from config. These are all module-level constants.

**Spec update needed:** Either:
- Wire these keys into `config/settings.py` and read them in each module, OR
- Document that v0.3.0 uses code-level defaults (paper-tunable) and config exposure is deferred to v0.4.0.

The current hybrid — `Phase1Settings` with a subset of keys and the rest hardcoded — is misleading.

### No New Architecture Required

None of the merge requirements or deletion candidates imply a new architecture. Every fix is a refactor within the existing module-per-concern structure. No new frameworks, no abstract pipeline, no plugin system.

---

## Summary of Required Actions

| ID | Action | Files | Lines | Priority |
|---|---|---|---|---|
| M1 | Extract shared `compute_enrichment()` helper | `market_data.py`, `market_data_sim.py` | ~15 new, ~40 replaced | **HIGH** |
| M2 | Unify CLI entry-point wiring | `main.py` | ~30 new, ~120 replaced | **HIGH** |
| M3 | Move soft warnings to filter layer | `scanner/attention.py` → `hard_filters.py` | move ~200 lines | **MEDIUM** |
| D4 | Remove dead candidate lifecycle code | `state_machine.py:132-152` | delete ~20 lines | **LOW** |
| D5 | Add placeholder comment | `entries.py:224` | 1 comment line | **LOW** |
| Spec | Add market_data.py to module map, clarify hard-vs-soft split | `docs/SPEC.md` | ~5 lines | **MEDIUM** |
| Spec | Document hardcoded config vs code constants | `docs/SPEC.md` §17 | ~10 lines | **MEDIUM** |

**Total new code:** ~45 lines
**Total deleted/replaced:** ~180 lines
**Net reduction:** ~135 lines

---

## Negative Space — What Was NOT Found

- ❌ No Gen 2 importable modules remain in `src/`
- ❌ No `RossCameronPipeline`, pillars, regime, anti-patterns, quality scores, or AI gates in runtime
- ❌ No qualitative hard blocks (Chinese ADR, no-news, parabolic, low-float, biotech)
- ❌ No duplicate entry detectors, exit engines, or state machines
- ❌ No test duplication beyond the normal conftest consolidation already underway

The codebase is clean. The 5 overlaps identified are all within Gen 3, all are simple refactors, and none block the v0.3.0 paper-trial acceptance gate.

---

## Appendix — File:Line Reference Index

| File | Lines | Content |
|---|---|---|
| `src/market_data.py` | 140-172 | Derived enrichment (VWAP, EMA9, HOD, dollar vol) |
| `src/market_data_sim.py` | 106-125 | Duplicated derived enrichment |
| `src/main.py` | 106-409 | 4 near-identical entry-point implementations |
| `src/scanner/attention.py` | 440-638 | Soft warnings misplaced in scanner module |
| `src/hard_filters.py` | 346-351 | Time gate constants |
| `src/exits.py` | 363 | Flatten time default |
| `src/state_machine.py` | 132-152 | Dead candidate lifecycle code |
| `src/entries.py` | 42-63, 224 | Bar dataclass, placeholder comment needed |
| `src/models/schemas.py` | 1-344 | Single source of truth for all data models |
| `src/decision_pipeline.py` | 179-440 | Pipeline orchestrator (no duplication) |
| `src/paper_execution.py` | 1-632 | Execution gateway (clean inheritance) |
| `src/sizing.py` | 1-105 | Pure math (no duplication) |
