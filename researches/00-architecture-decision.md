# Architecture Decision Record — v0.3.0 Implementation Source

Date: 2026-06-13

Status: Accepted

Decision owner: Implementation planning audit

## Decision

Alpacatrader v0.3.0 implementation work must continue from the current Gen3 rebuild code only.

The root `SPEC.md` created from this audit is the source of truth for the next implementation pass. The existing codebase, `docs/SPEC.md`, historical handoffs, and prior research reports are evidence and background. When they conflict with root `SPEC.md`, root `SPEC.md` wins.

## Why This Decision Was Required

The handoff that requested this audit described three generations of code as coexisting in the repository. That description is stale.

The current repository contains only the Gen3 rebuild source under `src/`. Gen1 and Gen2 modules are physically absent and are guarded by negative-import tests.

Evidence:

- `tests/test_no_legacy_modules.py:15-48` lists deleted legacy module paths including `src.pipeline.v3_pipeline`, `src.anti_patterns`, `src.pillars`, `src.regime`, `src.execution.engine`, `src.providers`, and old model/provider modules.
- `tests/test_no_legacy_modules.py:59-82` asserts those modules and packages cannot be imported.
- `tests/test_cli_rebuild.py:1-11` and `tests/test_cli_rebuild.py:94-133` guard the CLI path against `RossCameronPipeline` and `v3_pipeline` imports.
- `researches/02-dead-imports-crossrefs.md` found zero missing local imports, zero circular dependencies, and zero cross-generation import violations.
- `researches/04-overlap-redundancy.md` concluded Gen2 is fully purged and Gen3 is the canonical implementation.

## Canonical Architecture

The canonical runtime architecture is:

```text
CLI/settings
  -> TradingApp loop
     -> startup reconciliation
     -> monitor open positions first
        -> market snapshot
        -> exit engine
        -> execution gateway
     -> scan candidates second
        -> scanner/enrichment
        -> market snapshot
        -> attention ranking
        -> data confidence
        -> soft annotations
        -> mechanical hard filters
        -> move classifier
        -> entry detector
        -> sizing
        -> execution gateway
        -> protection
        -> decision journal
```

The implementation must remain module-per-concern and Gen3-only. It must not resurrect Gen1/Gen2 packages, classes, strategy gates, or qualitative hard filters.

## Source-Of-Truth Order

For v0.3.0 implementation, use this precedence:

1. Root `SPEC.md`.
2. Current audit reports in `researches/00-06` as evidence for why the spec says what it says.
3. Current source files and tests as the implementation baseline.
4. `docs/SPEC.md` only as historical strategy background until it is reconciled into the root spec.
5. Historical handoffs and older research only when they do not conflict with the current repository audit.

## Explicit Non-Decisions

- Do not rewrite the application into a new framework.
- Do not reintroduce `RossCameronPipeline`, pillars, regime filters, anti-pattern hard blocks, AI/LLM gates, legacy providers, or legacy execution engines.
- Do not treat Chinese ADR, no-news, biotech, low float, parabolic, or speculative labels as mechanical hard rejects.
- Do not implement code as part of this planning deliverable.

## Consequences

- Root `SPEC.md` becomes the implementation contract.
- Current code that contradicts `SPEC.md` is a backlog item, not a reason to weaken the spec.
- Tests must be updated to enforce the spec before v0.3.0 is considered complete.
- Existing `docs/SPEC.md` should later be reconciled or marked historical to avoid two competing specifications.
