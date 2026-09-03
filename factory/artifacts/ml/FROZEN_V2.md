# FROZEN CONFIG v2 — pre-registered before fresh-window evaluation (2026-09-02)

Committed BEFORE any 2026-04..08 return was computed or examined. No parameter may be
changed after the fresh-window run; no tuning between months. Derived exclusively from
2025-05..2025-12 (corrected rvol data) + dev-month thresholds.

## Data layer
- Source bars: data/clean_ohlcv_{YYYY-MM}.parquet (RTH-only, close≥2 & vol≥100 pre-filtered).
- rvol numerator: close×volume cumsum over ticker×et_date on **timestamp-sorted** rows
  (sort fix in build_features.rvol_attach; verified by tests/test_feature_parity.py and
  tests/audit_features_independent.py: hand==parquet==engine, 1253 rows, 0 mismatches).

## Model
- factory/artifacts/ml/model_v2.pkl — LightGBM, 30 feats (FEATS order below, fixed),
  trained on 2025-05+2025-06 (243,837 events), dev 2025-07 (139,400), best_iter=56.
- score = model.predict(X, num_iteration=model.best_iteration)
- log_dollar_volume = log1p(cum_dv) (derived, not stored).

FEATS = ["pct_gain_grid","rank","n_hod_breaks","dip_5m","trap_reclaim","dip_depth_5m",
         "vwap_dist","above_vwap","dist_open","open_gap","dist_hod","range_pos",
         "log_close","log_dollar_volume","dv_5m_rate","dv_accel","rvol","excess_gain",
         "market_ret_5m","tod_min","dow","ret_1m","ret_3m","ret_5m","ret_10m","ret_15m",
         "ret_30m","realized_vol_15m","efficiency_30m","n_up_bars_15"]

## Thresholds (dev-only, May–Jul p90/p97 of v2 scores)
- theta = 0.00098
- theta_hi = 0.00259 (unused by E6; kept for S4 diagnostics only)

## Stream construction (per month, all events)
1. pool = scored events with fwd60_t1entry not null (model-scoreable set)
2. vis_rank = ordinal rank of score, descending, within (et_date, tod_min)
3. M3 = vis_rank ≤ 2 AND score ≥ theta

## Composite entry filter (frozen: gates selected on 2025-08..10, validated +86.1bps Nov–Dec)
- composite = M3 AND rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270

## E6_rvol8 exposure structure (frozen)
- Within composite stream, per ticker-day: first entry when rvol > 8
- re-entry allowed only ≥ 61 minutes after previous entry (same ticker-day)
- max 3 cycles per ticker-day; entry minute ≤ 328 (relative); cum_dv ≥ $5M at entry
- exit X_60m: fwd60_t1entry (next-bar entry, 60-minute hold, close-to-close)

## Economics
- unit $10,000; round-trip cost 20bps (40bps sensitivity reported); max 10 concurrent
  positions; 1 position per ticker; entry capital = first-fit when slots free.

## Evaluation protocol (pre-registered)
- Fresh window: 2026-04, 2026-05, 2026-06, 2026-07, 2026-08 — never touched by any
  selection, threshold, or structure decision.
- ONE pooled run + month-by-month breakdown. No inter-month tuning, no re-selection,
  no parameter changes regardless of result.
- Report: entries, net/unit @20bps and @40bps, wr, monthly breakdown, pooled mean/day
  with p10/p90, and PnL concentration (top-5 trades' share of gross PnL).
- Decision rule (stated in advance): paper-deploy track requires pooled net/unit > 0
  at 20bps with all five months ≥ −20bps/unit. Anything else = no-go, investigate
  mechanism (not re-tune).

---

## EVALUATED (2026-09-03): **NO-GO**

Single pre-registered pass on 2026-04…08 (Alpaca SIP backfill, validate_backfill PASS).
Pooled −41.6bps/unit @20bps, −61.6 @40bps, wr 0.480; all five months negative
(−16 … −153bps); 215 entries (2.9/day); −$115/day; top-5 = 13.2% of |gross| (broad, not
tail-driven). Both deploy conditions failed. Full tables + post-hoc decomposition:
REPORT_FRESH_WINDOW_2026.md. No re-tuning on the fresh window. Paper bot continues as
measurement instrument only.
