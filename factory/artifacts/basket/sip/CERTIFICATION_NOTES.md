# SIP certification notes — legacy anatomy vs SIP (BASKET-01 data upgrade)

Purpose: determine, empirically, whether the legacy HF/Finnhub-derived minute bars are good
enough for BASKET's questions, or whether Phase-1 measurements must be regenerated from SIP.
Method: `factory/scripts/sip_certify.py` reconstructs the same facts from SIP raw trades/quotes
and compares them event-by-event with the committed anatomy. `--why TICKER --day D` prints the
legacy vs SIP bars around a member's fill for human review. Nothing is redefined; all rulers,
populations and rules stay as frozen.

## Day 1 (2021-02-01, first completed panel day) — headline diffs

- selection: 34 rank-flip positions across 13 snapshots (re-rank of the stored top-10 by SIP
  prices); px delta p50 ≈ 2bps.
- path: 38 members; fill delta p50 0bps, p90 +53.5bps, **max 469bps**; MFE delta p90 +47bps,
  **max 492bps**.
- first-passage: **21 bar-based order flips** (legacy bars vs SIP-derived bars) across 4
  members; 1/1 stored AMBIGUOUS case resolved by raw-trade sequencing.
- execution: spread at fill p50 ≈ 107bps, p90 ≈ 358bps (quoted spread at the entry moment).
- tail: 10 symbols with SIP RTH price-updating max differing from the stored day high by >0.5%,
  **max +9.3%** (CDE 12.58 → 13.75).

## Why disagreement happens — verified mechanisms (not hypotheses)

The legacy tape is a *sparser* record than SIP. Three mechanisms, each verified against raw
bars for 2021-02-01:

1. **Missing minutes.** DCOM 2021-02-01: legacy has NO bar at et=571 (09:31). SIP has one
   (o=25.37, l=25.04, v=640). The stored A_open fill is therefore the *next* legacy bar.
2. **Single-print / subsampled minutes.** DCOM et=570 legacy: o=h=l=c=25.11, v=2071; SIP same
   minute: o=25.11 h=26.0 l=24.48, v=3817 — a 3.3% intra-minute range the legacy bar lost.
   Legacy et=572: lone print 24.835 (v=478); SIP et=572: o=25.35 l=24.54 (same v=478).
   The stored fill 24.835 is a print that does not appear in the SIP record at all.
3. **Range differences crossing rulers.** YGMZ 2021-02-01 (B/590): legacy et=592 low 41.81 vs
   SIP 41.321. The −3% ruler from the fill sits at 41.71 → SIP touches it, legacy does not →
   stored "up-first" becomes SIP "down-first" for the same member. YGMZ B/575: SIP et=576 high
   41.78 vs legacy 41.24 (and SIP et=577 h=41.78 vs legacy 41.50).

Consequences observed (descriptive; not conclusions):
- fill prices shift (missing/subsampled bars change *which* bar is first);
- barrier ordering flips when a range difference crosses a ruler (this is the C-question,
  and it is real, not an artifact of the comparison code);
- highs/lows are understated in the legacy record (tail measurement at risk);
- ranking can shift when decision prices come from lone prints or missing bars.

## Scope limits of the pilot (explicit)

- Only the anatomy candidate union (top-10 per snapshot + winners; quotes for top-3) was
  ingested; names never appearing in the legacy top-10 are not re-ranked (a lower bound, not
  a full "who would SIP have selected" answer). Full-universe regeneration remains a decision
  after the panel.
- prev_close is taken from the legacy carry (not re-derived from the previous SIP session).
- A_pm ranking is not re-derived (premarket ingestion is 2025-only).
- Spreads are quoted spreads at the fill moment; no fill model is applied.

## Artifacts

- `factory/artifacts/basket/sip/certification_<day>.json` — per-day comparisons + examples.
- `factory/artifacts/basket/sip/certification_panel.json` — A–E summary over processed days.
- `data/sip/` — canonical raw layer (gitignored): trades/quotes parquet + manifests.
