# Fresh-Window Verdict — 2026-04…08 (evaluated 2026-09-03)

Pre-registered one-shot evaluation of FROZEN_V2.md (model_v2 + theta 0.00098 + M3 +
composite + E6_rvol8 + X_60m, cap 10, $10k units) on the untouched 2026-04…08 window.
Data: Alpaca SIP backfill (validated vs HF ground truth on 2026-03: 100% listed-ticker
bar coverage, 99.5% of closes within 0.5%, 99.1% of volumes within 1%; validate_backfill
PASS, 2026-09-02). Features built by the fixed pipeline (post-rvol-corruption). No
parameter was touched after the freeze commit; no performance numbers were viewed before
this single eval run (eval_frozen_2026-04_08.log).

## RESULT: NO-GO (both pre-registered conditions failed)

| month | entries | taken | net20/unit | wr | net40/unit |
|---|---|---|---|---|---|
| 2026-04 | 48 | 45 | −0.450% | 0.511 | −0.650% |
| 2026-05 | 66 | 63 | −0.160% | 0.524 | −0.360% |
| 2026-06 | 23 | 21 | −1.532% | 0.381 | −1.732% |
| 2026-07 | 38 | 36 | −0.158% | 0.444 | −0.358% |
| 2026-08 | 40 | 37 | −0.430% | 0.459 | −0.630% |
| **POOLED** | **215** | **215** | **−0.416%** | **0.480** | **−0.616%** |

- Pooled net/unit = **−41.6bps @20bps** (FAIL: required > 0)
- 2026-06 = −153.2bps (FAIL: required every month ≥ −20bps)
- Economics: 73 trade-days, mean **−$115/day**, p10 −$919, p90 +$817, 44% positive days
- Concentration: broad, not tail-driven — top-5 trades = 13.2% of |gross| (BIRD +27.7%,
  AEVA +13.1%, NMAX +12.0%, TRAX +11.3%, FJET +10.7%)
- Flow healthy: 215 entries ≈ 2.9/day (2025 reference 2.3/day) — a performance failure,
  not a flow collapse
- Sampling noise: n=215, per-trade σ≈5–6% ⇒ se(mean)≈38bps. The pooled −41.6bps is ~1σ
  from zero; what is UNAMBIGUOUS is the absence of the 2025-level edge (+45–104bps) and
  five consecutive negative months.

## Post-hoc decomposition (labeled post-hoc; no decisions taken from it)

Event-level, label-complete population, @20bps:

| window | IC(score,fwd60) | M3 net | composite net | E6-pool net |
|---|---|---|---|---|
| 2025-11/12 (val) | +0.0589 | +0.123% | +0.863% | +1.038% |
| 2026-01..03 (Q1) | −0.0041 | −0.248% | −0.325% | −0.783% |
| 2026-04..08 (fresh) | +0.0181 | +0.025% | −0.134% | +0.092% |

- The model's raw ranking retains weak signal in the fresh window (IC +0.018,
  D10−D1 = +29bps) — about a third of 2025 validation strength.
- The rvol>4 & vwap_dist>0.03 composite gates — the strongest additive factor in 2025
  (+0.123%→+0.863%) — did NOT transfer (fresh: +0.025%→−0.134%). The exposure pool
  (rvol>8) is ~flat (+0.09%). What broke is the 2025-conditional extension/volume
  pattern, not primarily the model.
- E6 structure picks (first-of-episode + 61-min re-entries, ≤3/day) averaged −42bps
  while the underlying qualifying-minute pool averaged +9bps — within-episode timing
  contributed negatively; with n=215 this split is noise-dominated.

## Verdict

The frozen composite/E6 stack has no validated edge on 2026-04…08. Per the
pre-registered rule: NO paper deployment of this stack, no re-tuning on the fresh
window. The only surviving (weak, unvalidated) signal is model rank IC ≈ +0.02 —
below any deployable threshold. Any further strategy work on this family requires a
new hypothesis and new data (2026-09 onward), not gate re-selection on 04–08.
