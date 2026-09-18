# SIP certification → Phase-1 regeneration decision (BASKET-01)

Status: pilot panel complete — 20 days, 260 snapshots, 695 member-days. Evidence:
`SIP_DECISION_NOTE.md` (A–E numbers), `SIP_TRIAGE.md` (why each revision happened),
`certification_<day>.json` (event-level), `data/sip/manifest.jsonl` (provenance/checksums).
No rule, parameter or thesis change. PRE-REG-BASKET-02 freeze remains PAUSED.

## A–E summary (panel)

- **A selection**: top-3 SET changed on 15.4% of snapshots; 763 rank-flip positions;
  590 fetched outsiders above the 10th stored member; decision-price deltas p50 ≈ 1 bp
  (the churn is at the margin, not wholesale).
- **B path**: |fill delta| p50 0 / p90 77 bps; 7.5% of member-days > 100 bps;
  MFE deltas p90 ≈ 96 bps (excluding the artifact max).
- **C first passage**: **498** non-ambiguous +H/−L order flips across 695 member-days
  (all on healthy-coverage symbols), 27/27 stored-ambiguous cases resolved by trades.
- **D execution**: quoted spread at causal fill p50 86 bps / p90 472 bps; market state
  only — no fills assumed.
- **E tail**: 50 stored member-days ≥ +100% → 49 confirmed; 1 unconfirmed = BRP's
  fabricated spike. No SIP-only new big member in the panel.

## Why the large revisions happened (all decoded)

- **BRP 2022-03-10** — legacy bad print (1092.71/785.47 absent from SIP; true max ~26.4).
  Removed correctly by the new layer.
- **BNY 2025-10-30** — scale offset ×10.6 (SIP all day 106–109; legacy ~10.3 across days).
  Percent measures safe; absolute levels not comparable.
- **GOLD 2025-03-24** — scale offset ×1.49 (SIP ~27.3–28.9 all day; legacy ~18.9–19.1).
- **14 coverage-low diffs** — SIP archive gaps (e.g. BKKT/BE/RDW 2021-10-25 have
  auction prints only; live probes confirm 0 trades). SIP is NOT truth there; legacy
  stands unsupported → comparisons void, not resolved either way.

**Control**: 2026 panel days (same Alpaca provenance as the legacy 2026-03+ backfill)
show 0 rank flips, 0 set changes, only minor tail diffs — the machinery returns ~zero
when feeds match.

## Verdict

**Regenerate Phase-1 from SIP-derived bars** (the stronger of the two options).
Rationale: first-passage ordering materially changes when resolved to trades (498 flips,
healthy coverage); basket membership at the margin changes (15.4% set changes); and the
legacy substrate carries confirmed bad prints and scale offsets that no patch list can
fully whitelist. Selective regeneration (keep legacy selection, rebuild path layers) is
rejected because the selection layer itself moves.

## Required guardrails for regeneration (if owner approves)

1. Per-symbol-day SIP coverage QC (RTH trade count/span) — gap days excluded, never
   silently scored; blocked-slot reporting like the legacy QA.
2. Bars rebuilt per Alpaca's documented condition table (already implemented in
   `sip_bars.py`); auction/print codes (Q/M/O/5/6) preserved as a side channel.
3. Candidate net widened beyond the legacy candidate union (legacy gain-floor superset +
   PIT universe) so SIP top-10s are not restricted to legacy-selected names.
4. Quotes ingested for candidate names only (execution truth at causal entries);
   market-state vs assumed-fill layers stay separate.
5. Reserved months 2026-06..08: raw acquisition allowed, outcomes untouched until the
   frozen implementation is confirmed once.
6. No quote-feature mining, no strategy changes, no parameter work during migration.

## What this does not decide

The thesis, populations, rulers and measurement contract are unchanged. This note only
answers "is the legacy record a sufficient measuring instrument?" — it is not, and
better measurement is available. Awaiting owner review before any backfill or
regeneration work begins.

## Addendum 2026-09-18 — guardrail #3 refined: measured acquisition net sizes

Guardrail #3 asked for a candidate net "widened beyond the legacy union (legacy gain-floor
superset + PIT)". Measured on 8 era-spread days (`factory/scripts/sip_net_size.py` →
`net_size.json`), the gain-floor formulation is impractical:

| net definition | names/day |
|---|---|
| legacy snapshot union (current panel net) | 38–51 |
| per-T top-10 union, B only | 11–21 |
| gain floor ≥ +2% at any T | 1,587–3,162 |
| gain floor ≥ +5% | 494–2,096 |
| gain floor ≥ +10% | 141–1,049 |
| **cutoff-margin net: within 1% of the 10th-ranked score at each T** | **11–23** (4–11 beyond legacy) |

Rejected: the gain floor is 30–70× the panel volume while reaching names far below any
plausible top-10 cutoff. Adopted definition: the **cutoff-margin net** — every PIT name
within 1% of the legacy 10th-ranked decision score at any frozen T, anchor = max(gain vs
RTH open, gain vs previous close). The 1% margin is 3–4× the largest measured
decision-price error in the certification panel (p90 ≈ 24bps, max ≈ 266bps).

Recommended per-day acquisition net = legacy snapshot union ∪ winners lists ∪
cutoff-margin net (1%) ∪ A_open cutoff-margin at 09:30 ≈ 55–70 names/day
(≈1.1–1.3× the panel) → ≈5.5–6.5 min/day single-process → 1,065 dev days ≈ 4–5 days;
4–6 parallel processes ≈ ~1 day. Rate-limit probes: 40 sequential requests 1.3 req/s;
90 requests / 6 workers 2.8 req/s; 120 requests / 12 workers 2.7 req/s; zero 429s at
≤164 req/min (under Alpaca Basic's documented 200/min; higher tiers untested).

Residual risk: a name whose *legacy* decision score is understated by >1% could be
missed. Post-backfill check: count promotions from beyond the margin (expected ~0).
