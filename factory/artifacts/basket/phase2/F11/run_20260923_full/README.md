# F11 — execution realism and capacity

**Evidence:** [RUN] complete stored-open friction ladder over 60 unique frozen F1 strategy configurations (F1 has 120 rows including 100/150-bps duplicates) × 5 round-trip friction values = 300 runs; each covers the same 1066 permitted development dates. Full row-level surface and run hashes are in `surface.json`; per-run artifacts are under `runs/`.

## Observed friction surface

For each ladder value, the mean range across F1 cells (unit-normalized basket-day return):

| Total round-trip friction | Mean range across all cells |
|---:|---:|
| 0 bps | -0.036649 to -0.010815 |
| 50 bps | -0.041341 to -0.015376 |
| 100 bps | -0.046009 to -0.019914 |
| 150 bps | -0.050653 to -0.024429 |
| 200 bps | -0.055275 to -0.028922 |

Every cell's full-development mean remains below zero at all five registered friction points; the stored-open baseline does not survive this descriptive unit-capital net-return screen. No single cell is selected.

### Dual development blocks — mean ranges across the whole surface

| Friction | 2021-02–2023-12 | 2025-02–2026-05 |
|---:|---:|---:|
| 0 bps | -0.039713 to -0.013009 | -0.044059 to 0.000083 |
| 50 bps | -0.044391 to -0.017338 | -0.048486 to -0.004686 |
| 100 bps | -0.049045 to -0.021645 | -0.052891 to -0.009431 |
| 150 bps | -0.053677 to -0.025931 | -0.057274 to -0.014152 |
| 200 bps | -0.058285 to -0.030196 | -0.061636 to -0.018850 |

This is a whole-surface descriptive range, not a selection among cells. Existing 100/150-bps F1 runs are reused after config/date validation; 0/50/200 bps are rerun through the unchanged contract simulator. Dual development blocks are recorded per cell in `surface.json`.

## Execution/capacity scope and limitations

- **stored_open_friction_ladder — RUN:** all frozen F1 cells; same stored entries/exits and C0=1
- **conservative_minute_execution — BLOCKED:** PRE-REG-BASKET-02 §3.10 gives no exact minute fill rule; bars exist, but selecting a price rule would invent execution semantics.
- **quote_aware_execution — BLOCKED:** raw SIP quote parquet files are absent from data/sip/quotes; committed pilot sample manifest is metadata, not quote observations.
- **capacity_curve — BLOCKED:** canonical net_size.json is an eight-date universe-fetch-size measurement (eligibility/cutoff counts), not a position-volume participation surface; account size/order notional is intentionally unset.

Canonical minute bars matched 16,684 of 16,684 unique selected entry bars. Entry-minute dollar-volume and high-low-range percentiles, plus frozen simulator blocked, pending, and carry counts, are in `surface.json` under `minute_input_facts`.

Canonical `net_size.json` covers eight era-spread days and counts PIT-eligible universe names by gain floor / legacy top-10 cutoff margin. It does not contain trade-size participation, order notional, or realized impact, so no deployable-notional capacity curve is manufactured from it. Those eight days are input facts only, not full-dev evidence.

## Evidence boundary

Execution price rules, strategy semantics, C0 and the F1 unit capital convention remain unchanged. No account size is chosen. No quote/minute fill assumptions are imputed where the pre-registration and data do not support them. This development evidence is not a profitability, deployment, or live-readiness claim.

Pre-registration: `researches/PRE-REG-BASKET-02.md` §3.10.
Mechanics: `factory/BASKET-SIM-CONTRACT.md`.
Canonical baseline: `factory/artifacts/basket/phase2/F1/README.md`.
