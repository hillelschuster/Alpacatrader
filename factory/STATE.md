# STATE.md — Top-Gainer Research Factory

**Last updated**: 2026-08-28
**Phase**: 2025-06 AND 2025-05 CERTIFIED (both with microstructure adjudications),
on Drive. H006 tail + H009 faithful replicated on May: NOT ROBUST (H006 edge →~0,
H009 edge halved). H007/H010 killed. Next: download+certify 2025-07 as the true
OOS month for H006/H009; run H008.

## ML Phase — Overnight Mission (2026-09-01, in progress)

Pivot approved by user: systematic ML pattern discovery on the certified event
stream, replacing hypothesis-by-hypothesis testing (all ten H001–H010 now tested;
H008/H009 live on as features).

- **Spec**: `factory/artifacts/ml/FEATURE_SPEC.md` v1 + oracle review (design
  critic subagent, ADOPT/MODIFY verdicts) + reviewer leakage audit (10 findings,
  3 must-fix code items — all implemented).
- **Target**: raw `fwd_ret_60m` winsorized ±10% (constant cost shift moved to
  evaluation). Cost scenarios 20/40bps RT at eval only.
- **Sampling**: `sample_weight = 1/n_events(episode)`, cap 120 (episode-equal
  influence). Evaluation always on FULL population.
- **Splits**: train 2025-05+06, dev 2025-07 (burned: early stop + threshold only),
  final OOS 2025-08..12 certifying via `cert_chain.sh`. Nov–Dec frozen as re-OOS
  if August is ever peeked. Rolling-origin added later if first result is alive.
- **Feature build** `factory/scripts/build_features.py`: minute grid per event
  (ticker,et_date), close/high ffill, low/volume NULL on missing bars (real bars
  only for dips + vol stats); H008 RVOL baselines (null at 09:30 — expdv=0 trap);
  H009-faithful trap/reclaim on grid (`hod_before = hod.shift(1)`); range_pos via
  running cum extremes (reviewer HIGH fix); gain_vel time-based; market_ret_5m
  within-session; t+1m-entry label variants (executable-entry proxy; edge must
  survive both entry timings). Outputs `data/ml_features/features_YYYY-MM.parquet`
  (gitignored) + `coverage_YYYY-MM.json`.
- **Coverage 2025-07** (139,400 events): rvol .896, ret_30m .942, vwap_dist 1.0,
  trap_reclaim 1.0, market_ret_5m .993, fwd_ret_60m .738, fwd60_t1entry .683.
- **Training** `factory/scripts/train_ml.py`: LightGBM (single config, depth 6,
  min_data 200) + ElasticNet baseline; decile tables day-ranked; episode-best
  (one trade per ticker-day); day-clustered bootstrap CIs; daily-PnL drawdown;
  Spearman IC; t+1-entry variant; long+short; @20/40bps. Aug–Dec evaluated once,
  frozen.
- **Accepted biases (documented, not fixed)**: non-PIT universe tags (yfinance
  2026 fetch; PIT archive = future option); certify `n_bars>=30` full-day gate
  conditions on the future (survivor-flavored population; uniform across months);
  H009 dip window 5 grid-min (tighter than H009's 5 bars post-halt).
- **2025-03 downloaded late** (May 1 baselines reach into March); May features
  rebuilt after March clean.

## ML v1 OOS — VERDICT: GO (2026-09-01)

Single frozen pass (protocol pre-registered in `factory/artifacts/ml/OOS_PROTOCOL.md`):
LightGBM depth-6 (57 iters), train May+Jun, dev Jul, OOS Aug..Dec (293,687 events).
**5/5 gates PASS.** Details: `factory/artifacts/ml/REPORT_OOS_v1.md`.

- D10 (top-10% score/day) all-events net @20bps: +10/+49/+23/+20/+6 bps monthly,
  pooled +22.1bps, n=49,200. t+1m-entry pooled +22.4bps (entry-timing robust).
- wr gradient D1→D10 monotone in all 5 OOS months; D1 = −39..−92bps (avoid zone).
- @40bps D10 dead (−25..−37bps) → edge requires ≤30bps RT execution.
- ElasticNet reproduces ordering 5/5 (structure not a tree artifact).
- Phase 6 composite rv>4 & vwap_dist>3% & tod<270, frozen on Nov–Dec: **+89.4bps
  @20bps, wr .605 (n=3,847 full set)**; survives 40bps in label-complete subset.
  Adversarial review: D10 GO claim audited CLEAN; composite PARTIALLY validated —
  the design grid was informed by an earlier slice peek at Nov–Dec (disclosed in
  REPORT_OOS_v1.md). Definitive test = exact frozen rule on next fresh month.
  Script: factory/scripts/composite_ml.py. Sizing research next.
- Dead at: D10@40bps; episode-best picking adds nothing (breadth portfolio).
- Next: pre-register composite rule (rv>3 & vwap+2% & tod<240 built from Aug–Oct,
  validate Nov–Dec untouched); bot integration is a recommendation only (bot off-limits).

## 2025-06 Certification — DONE (2026-08-28)

Internal gates (certify_month.py, `factory/artifacts/certification_2025-06/`):
20 sessions, 21.99M rows, 14,213 tickers → 2,370 candidates (86 split suspects,
1,448 universe-excluded, 217 universe-unknown), **120,054 events**; per-day
5157/5918/6640; pct_gain p50 11.29% / p90 22.45% / p99 50.44% / max 148.51%.
All internal gates PASS (checks.json). Last clean bar ~15:59 (no exact 16:00 bar).

External verification (verify_external.py, 20 ticker-days, seed 42):
pct_gain@EOD PASS (95%, 0.042pp) · splits PASS (100%) · rth_hod vs yf High PASS
(100%, 0.0083%) · 1-min path PASS (4.14bps) · prev_close PASS at operating
tolerance (95% within 0.35%). The only median-gate breaker (UAMY 2025-06-06
+0.662%) was adjudicated as a genuine 15:59 print (56,586 shares) vs yf's 16:00
auction close 3.00 — microstructure, not error; the 0.05% median gate is
miscalibrated for microcaps. Details: external_verification_report.md.
Survivorship caveat stands (current-state exchange tags, not PIT snapshots).

## 2025-05 Certification — DONE (2026-08-28, adjudicated)

Internal gates (`factory/artifacts/certification_2025-05/`): 21 sessions, 22.5M
rows, 14,058 tickers → 3,056 candidates, **123,783 events**; pct_gain p50 13.65%
/ p90 28.86% / p99 63.70%. First session 2025-05-01 dropped (no April locally).

External verify (seed 42): after **split-normalization of the yf reference**
(yf serves split-adjusted OHLC in both auto_adjust modes; ref = yf_price ×
product of split ratios with ex-date AFTER the sampled session — e.g. CVNA
5:1 split 2026-05-08 turned a false 79.95% diff into −0.243%), residual
prev_close/pct_gain/rth_hod offenders (MNTN 05-23, GCL 05-21, HNGE 05-27)
were adjudicated from bars as genuine print-vs-16:00-auction microstructure
(hyper-volatile IPO / dead-tape-then-hot / healthy-tape print). Splits + 1-min
path PASS. Verdict: **ADJUDICATED PASS — 2025-05 CERTIFIED**. Same 0.05%
median-gate miscalibration note as June. Full evidence in
certification_2025-05/external_verification_report.md.

## May Replication of Provisional Positives — NOT ROBUST (2026-08-28)

- **H006 (>8% VWAP fade)**: May n=2,470, exp_net ~0 at 20bps RT (June +0.49%/15m);
  40bps RT negative (−0.19%). 12%+ tail −0.33% vs June −3.29%. **DOWNGRADED
  survivor-provisional → not robust.**
- **H009 faithful subset**: direction preserved, edge halved (+21.5bps @60m vs
  +28.6; 30m +13.4bps not net-positive); 40bps negative. Dilution 91.5% stable.
  **Weak replication only.**
- Comparison: `factory/artifacts/replication_2025-05_vs_2025-06.md`.
- Implication: no hypothesis currently justifies building. The decisive test is
  a forward OOS month: download + certify 2025-07, run H006/H009 unchanged.

## 2025-07 OOS — COMPLETE (2026-08-31)

- **Certification 2025-07 DONE** (`certification_2025-07/`): 22 sessions, 24.36M rows, 14,611 tickers,
  2,657 candidates, 67 split suspects (< June's 86), 139,400 events (4,958–7,360/day).
  Certified with **BOTH** clean_ohlcv_2025-06 + 2025-07 files so 2025-07-01 sessions get the
  2025-06-30 prior-session close (user-corrected design; certified clean_2025-07 predates this fix).
- **External verification**: 1-min path PASS 20/20 (independent Alpaca IEX tape), split cross-check PASS 20/20;
  prev_close/pct_gain/rth_hod FAIL raw gates at 55%/85%/90% — statistically identical to May's
  adjudicated-and-accepted profile (50%/90%/90%; medians slightly better than May). Adjudicated ACCEPT
  (same reference artifacts: last-print vs 16:00 auction, consolidated-vs-feed highs). Run-note: first
  attempt had IEX checked=0 (missing python-dotenv in ephemeral uv env); re-run with --with python-dotenv.
- **H006 KILLED by July OOS** (EXP-H006-2025-07): tail 12%+ n=1,070 (4.3× June) exp −0.065% @20bps
  (June: +3.08% on n=247); monotone gradient absent; 8–12% bucket flipped positive.
- **H009 not executable standalone** (EXP-H009-2025-07): relative edge vs clean replicates 3/3 months
  (+13.7bps @60m July-only, halving each month: 28.6→21.5→13.7) but absolute net ≈0 @20bps, negative @40bps;
  June's 10–11am concentration absent in July. Faithful-subset filters reconciled (May used strict,
  June used loose — both now reported for all months).
- Comparison: `factory/artifacts/replication_2025-05_06_07.md`.

## Where We Are (summary)

H001–H005 (continuation-family hypotheses) were tested on 2025-07 and all killed
(net negative after 20bps roundtrip, universal OOS decay). Results are recorded but
carry a **data-quality caveat**: they ran on a pipeline whose `prior_close`,
split-handling, and universe filtering are not yet trustworthy (details below).
The verdicts are probably still correct (edges were negative, not falsely positive),
but no further conclusions may be built on this foundation until one month is
certified end-to-end.

**Current objective**: certify 2025-06 (chosen because July 2025 is burned by
H001–H005 experiments; May 2025 remains available as trailing baseline history).
Then set up the bounded Google Drive artifact workflow, then resume hypotheses
(H006–H010) on certified data.

## Certification Gates (2025-06 must pass all)

1. **Previous-session close**: `prior_close` must be the previous regular-session
   close per ticker (not "last bar before target date"). Spot-check vs yfinance
   raw daily closes: ≥99% of sampled ticker-days match within 0.1%.
2. **Splits**: detect overnight ratios far from 1 (e.g. <0.5 or >2) per ticker;
   exclude or adjust; cross-check sampled events against yfinance splits.
   No top-N event may span an unhandled split boundary.
3. **Universe**: NYSE/NASDAQ/AMEX common-stock filtering (exclude OTC/pinks,
   warrants, units, preferreds). PIT snapshots not yet downloaded — current-state
   exchange tags (yfinance/Alpaca assets) accepted for 2025-06 with noted
   survivorship caveat.
4. **Liquidity/participation**: events require price ≥ $2, cumulative dollar
   volume ≥ threshold (default $5M), and recent active volume.
5. **Intraday rank + true HOD**: recompute per-minute rank and RTH HOD; verify
   sampled minutes against independent recomputation.
6. **Independent path verification**: sample top-gainer ticker-days; compare 1-min
   bars and HOD against Alpaca IEX historical bars (free tier). IEX ≠ consolidated
   tape — use as sanity check with tolerance, not absolute truth. yfinance 1m is
   unavailable for 2025-06 (30-day window).
7. **Labels**: forward returns (1/3/5/15/30/60m), MFE/MAE spot-checked against bars.

Deliverable: `factory/artifacts/certification_2025-06/{report.md, checks.json,
events_topN.parquet}` — report states PASS/FAIL per gate with numbers.

## Known Data-Integrity Issues (verified in code, 2026-08-27)

- `clean_month.py`: PIT universe join and split exclusion are unimplemented stubs (`pass`).
- `rank_day.py` prior_close = last close before target date **within the monthly
  file** → (a) wrong when a ticker skipped sessions, (b) undefined on month's first
  trading day (ticker dropped), (c) raw unadjusted prices across split dates create
  fake huge % gains that win the rank.
- mito0o852/OHLCV-1m is **raw/unadjusted** (Finnhub). Splits MUST be handled
  explicitly. Audit found split signals (HMBL 890 jumps >20%, HCTI, OPEN, GIBO).
- ~25% of tickers sub-$1 OTC noise; price floor $2 only partially filters;
  no exchange/asset-type filter exists yet.
- Known duplicates (~(timestamp,ticker) dedup exists in both clean and rank steps).

## Datasets

| Source | Role | Status |
|---|---|---|
| `mito0o852/OHLCV-1m` | 1-min OHLCV backbone (raw, unadjusted) | 2025-05/06/07 downloaded+cleaned |
| `yolo22/stock-pit-archives` | PIT universe snapshots 2021+ | NOT downloaded |
| `speb/financial-data` stock_split_events | split dates | NOT downloaded |
| yfinance (installed 1.4.1) | external verification: daily raw closes, splits | available |
| Alpaca data API (IEX feed) | external verification: 1-min bars | keys present |

Data philosophy: sources are not sacred. Replace/supplement if materially better
reconstruction exists. Requirement = best practical data + correct methodology +
point-in-time integrity. Do not overengineer beyond what affects validity.

## Hypothesis Ledger (summary — canonical: HYPOTHESES.jsonl)

- H001 pullback-reclaim continuation — KILLED (2025-07)
- H002 HOD breakout — KILLED (2025-07)
- H003 gain×volume×TOD — KILLED (2025-07; inverted volume finding: high DV underperforms)
- H004 micro-pullback above VWAP — KILLED (2025-07; VWAP filter adds nothing net)
- H005 rank persistence — KILLED (2025-07; persistence less bad, still negative)
- Common failure: mean reversion dominates; 20%+ extremes worst; nothing cleared 20bps cost.
- CAVEAT: all on uncertified pipeline (prior_close/splits/universe gaps above).
  Treat as strong prior, not final truth.
- H006 VWAP-distance fade — **TESTED 2025-06 + replicated 2025-05: NOT ROBUST.**
  June (n=2,470): exp_net +0.493%/15m +0.581%/30m after 20bps RT; 12%+ tail
  (n=247, +3.09%/15m). May replication: exp_net ~0 at 20bps, −0.19% at 40bps;
  tail −0.33%. Edges concentrated in rare June tail; no cross-month stability.
  Artifacts: h006_results_2025-06/, h006_results_2025-05/.
- H007 consolidation-then-break — **TESTED 2025-06 certified: KILLED.** Contraction
  (15m range <1%) WORSE than grind control (≥1.5%): exp_net 60m −0.179% vs
  +0.241% (spread −0.420pp) net 20bps RT; all sub-segments negative; rolling-ATR
  arm untested. Artifacts: h007_results_2025-06/.
- H008 RVOL vs TOD baseline — queued (needs May 20-day baseline → May certification first)
- H009 failed-breakdown reclaim — **TESTED 2025-06 + replicated 2025-05: WEAK.**
  June faithful subset (dip below prior HOD → reclaim above, n=34,916): +20bps
  @30m, +29bps @60m net 20bps RT; broad trap bucket negative (91% VWAP-only
  dilution). May replication: direction preserved, edge halved (+21.5bps @60m),
  30m not net-positive, 40bps negative. Dilution 91.5% stable. Artifacts:
  h009_results_2025-06/, h009_results_2025-05/.
- H010 overnight gap / opening-range hold — **TESTED 2025-06 certified: KILLED.**
  Gap-up-hold continuation positive only at n=8/n=6 (collapses −3.75% @60m);
  stable positive is gap-up-FAIL (+0.51% @60m net 20bps — inverse of claim);
  gap-down segments n≤2 unusable. Required added $1M open-DV gate (62% of
  |gap|>5% universe thin/OTC). Degenerate OOS (19 signal days). Note: bars
  stamped at bar START. Artifacts: h010_results_2025-06/.
- RULE: single-month June results have degenerate OOS — provisional positives
  (H006 tail, H009 faithful) must replicate on a second month before further building.
- COST CONVENTION: experiment scripts compute roundtrip = cost_bps × 2 →
  `--cost-bps 10` = 20bps RT (H001–H005 standard).

## Storage / Google Drive Workflow — LIVE (2026-08-28)

rclone v1.75.0 at `~/.local/bin/rclone`, remote `gdrive:` configured (user's own
GCP OAuth client; shared client_id retiring 2026). Remote root:
`gdrive:algo-research/alpaca-top-gainers/`. Uploaded + MD5-verified
(`rclone check --download`, 0 diffs): `certification/2025-06/` (5 files incl
events_topN.parquet 8.3MB), `ledgers/`, `docs/`, `data/universe_tags.parquet`.

Gotcha: `rclone copy FILE DIR/FILE` creates a DIR named FILE — use `rclone copyto`
for single files. Upload valuable certified/derived artifacts only (no raw public
datasets; Drive is not a database). Don't let storage work delay research.

## Next Actions (ordered)

1. ~~Download + clean + certify 2025-07~~ DONE (2026-08-31, see above).
2. ~~Run H006 + H009-faithful on certified 2025-07~~ DONE: H006 killed; H009 direction-persistent
   but not executable standalone (ledgers updated).
3. ~~H008 RVOL vs 20-day TOD baseline~~ DONE (2026-09-01, EXP-H008-2025-06_07): relative edge real
   (+7..+28bps @60m, 6/6 volume-matched cells, 2/2 months) but absolute ≈0 to negative; stack with
   H009 dead in July. All ten hypotheses now tested on certified data; no standalone executable edge
   survives 20bps RT. Next: synthesis decision (see STATE.md Where We Are).
4. Upload new artifacts (July cert, May/June re-verified outputs, May
   replication artifacts, ledgers, STATE) to Drive + MD5 verify; prune local if
   disk >90%.

## Important Commands

```bash
# Download/audit/clean a month (HF source)
python factory/scripts/download_month.py --year 2025 --month 06
python factory/scripts/audit_month.py --file data/ohlcv_2025-06.parquet
python factory/scripts/clean_month.py --file data/ohlcv_2025-06.parquet

# Certification (new)
python factory/scripts/certify_month.py --file data/clean_ohlcv_2025-06.parquet --month 2025-06
python factory/scripts/verify_external.py --month 2025-06 --sample 20

# Legacy (used for H001-H005, superseded by certify path)
python factory/scripts/rank_day.py --file data/clean_ohlcv_2025-07.parquet --date 2025-07-02
```

## Key Paths

- Research root: `factory/`
- Canonical: `factory/RESEARCH_GOAL.md`, `factory/STATE.md`, `factory/AGENTS.md`
- Ledgers: `factory/HYPOTHESES.jsonl`, `factory/EXPERIMENTS.jsonl`
- Scripts: `factory/scripts/` (13 scripts: download/audit/clean/rank/extract +
  experiment_h001…h009)
- Data: `data/` (raw+clean 2025-05/06/07 parquet; ~6.5GB incl. ~3.8GB ranked
  intermediates — ranked files are reproducible, delete after artifact upload)
- Artifacts: `factory/artifacts/` (h001–h005 summaries/results,
  h001_h005_synthesis.md; ranked_*.parquet)
- Note: `factory/artifacts/decisions.jsonl*` and `data/journal/` are live-trading
  leftovers, not research products — exclude from research uploads.

## ML Phase 2 — live translation (2026-09-01 session)

Mission: convert D10 ranking into executable admission, understand archetypes, sequence
real trades, exploit composite, execution costs, fresh 2026 evidence, paper-bot spec.

### Live-causal admission (live_admission.py, frozen theta from May-Jul only)
| rule | n | net @20bps | @40 | t1 | months |
|---|---|---|---|---|---|
| REF whole-day D10 (non-executable) | 49,210 | +22.1bps | +0.2 | +26.3 | 5/5 |
| M1 fixed theta | 44,221 | +22.6 | +0.3 | +26.2 | 5/5 |
| M2 rolling-threshold | 51,137 | +19.2 | -0.8 | +22.4 | 4/5 |
| M3 vis-rank<=2 + theta | 35,186 | **+29.0** | **+9.0** | +32.8 | 5/5 |
| M4 first-crossing | 1,100 | +26.5 | +6.5 | +33.7 | 4/5 |
| M5 persist 2of3 | 42,822 | +24.9 | +4.9 | +28.2 | 5/5 |
| **M3 x composite** | 8,170 | **+90.5** | **+70.5** | wr .570 | 5/5 |
theta_fixed=0.00115 (May-Jul p90), theta_hi=0.00340 (p97). M3 = score-rank<=2 within
current minute's visible candidate set (et_date,tod_min) — fully causal, beats D10.
Flow: 332 events/day (M3), 83/day (composite).

### Sequencing (sequencing.py, t1-entry fills, OOS Aug-Dec)
First-signal entries are below stream average. S4 scale-in (unit1 first qualifying minute,
unit2/3 at later score>=theta_hi crossings, +5m/+20m spacing) best: composite stream
+77.4bps/unit, +130bps/episode, 5/5 months, 1.7 units/ep. Re-entry-after-cooldown WORST
(late-stage contamination). Chasing first-crossing per episode (M4) degrades vs M3.

### Archetypes (archetypes.py, OOS, post-peek descriptive)
- Money map (M3 stream): rvol>8 & vwap 3-8% +84bps; rvol>8 & vwap>8% +201bps;
  rvol 2-8 rows NEGATIVE on the M3 stream -> extreme volume is the separator, not the model score alone.
- Composite-internal gradients monotone: vwap_dist 3-5% +73 / 5-8% +89 / 8-12% +147 /
  >12% +419 (n=159, small); tod later = better within composite.
- KMeans k=5 inside composite: all 4 real clusters profitable +55..+183bps (broad region,
  not knife-edge). Best: parabolic monster-RVOL (rvol~364, vwap +8.7%, ret15m +5%).
- Blowoff trap: vwap_dist>10% on ALL events loses (-61bps) but WINS inside high RVOL
  -> extension is safe only with volume behind it.
- Execution: price>$20 loses at all costs; pocket = close<=20 & cum_dv 5-100M
  (5-20M best +220bps @20). <$5 survives 100bps. >$100M dv diluted.

### Fresh 2026 evidence (download->clean->certify->features, frozen eval)
2026-01/02/03 certified: 386,178 events / 61 days. eval_2026.py = frozen stack
(model_v1 + theta 0.00115 + M3 + composite + S4 + pocket) first OOS look. Results: see
factory/artifacts/ml/eval_2026.log. PAPER_BOT_SPEC.md = v0 live reproduction spec.

### Adversarial lanes — verdicts integrated
- oracle: decay ranking = (1) regime dependence of extreme-RVOL continuation, (2) execution-
  universe mismatch (~20-40bps haircut: 24.5% of composite rows lack a next bar; non-PIT tags),
  (3) layered post-peek selection. vis_rank sensitivity TESTED LOW (plateau <=1..<=4 within
  ~10bps; random 25% list drop: M3 29->24bps). rvol>8 = 17% of tape, capacity OK ($100-300K
  positions at 0.5-1% of day volume). vwap>12% cell = don't-cap-extension signal, not a cell.
  price<=20: trust directionally (stable sign at 4 cost levels). Recommended = pre-registered
  single 2026 pass. ADOPTED (PRE_REG_2026.md, written before any 2026 peek).
- reviewer: 1 MATERIAL — sequencing picks were conditioned on fwd60_t1entry non-null
  (60m-future tradability, unknowable live). FIXED: score-complete pool + decision-time
  tod<=328 cap; PnL on label-complete picks; null share reported (11.5%). S4 composite
  survived the fix (+77.5bps/unit vs +77.4 before). 5 MINORs fixed/noted (dead code,
  pool-definition documented, cut label cosmetic).

### 2026 FROZEN VERDICT (eval_2026.py, gates in PRE_REG_2026.md, written pre-peek)
2026-01/02/03 = 386,178 events / 61 days, model+theta+rules 100% frozen from 2025.
ALL 3 GATES PASS: composite net20 avg +77.3bps (gate >= +30), 3/3 months >= 0 (gate 2/3),
net40 avg +62.4bps (gate >= 0). Monthly @20: Jan +186 / Feb +5 / Mar +41. n=4,177, 70/day.
Score drift real: only 7.7% of 2026 events >= frozen theta (2025 OOS: 44%) -> strict regime
filter; DIAG month-own rel-p90 confirms attenuation is NOT a threshold artifact.
WHAT TRANSFERRED vs NOT:
- EVENT-LEVEL composite stream: 2025 +91 -> 2026 +87bps @20 (+62 @40). ROBUST. This is the product.
- Pocket (close<=20 & cumdv 5-100M, post-peek 2025 selection): first OOS look = +129.5bps
  @20 (n=1,973), monthly +345/-39/-4. Transferred (volatile Feb).
- Base M3 ranking: 2025 +29 -> 2026 +9.5bps; D10 ref -3bps; score edge now only in >t97
  tail (+39bps; t90-97 band -8.5bps). Attenuated, tail-only.
- ENTRY-TIMING refinements: S1 first-crossing 2025 +56 -> 2026 -2.5bps; S4 scale-ins
  +77 -> +18. DO NOT time within episode in v0; enter on stream events with per-name
  concurrency cap (expectancy ~= stream average, no timing skill needed).
Regime telemetry: rvol>8 share and composite flow did NOT collapse -> oracle mechanism #1
did not fire in H1-2026; mechanism #3 (layered selection) largely dead too.
RECOMMENDATION: forward paper bot = composite stream, event-level admission, 1 position
per ticker, 60m hold, 20-40bps budget, vwap extension unbounded. Sizing next.

### Exposure design (2026-09-01, exposure_design.py) — the conversion layer, SOLVED
Question: 17 qualifying minutes/episode -> how many real positions? Answer from evidence
(derivation 2025 Aug-Dec -> frozen replay 2026, PRE_REG_EXPOSURE.md written pre-peek):
- EXIT: fixed 60m hold dominates BOTH years. State-death exit destroys the edge
  (2025 +5.4 vs +54.6bps; 2026 +1.0 vs +34.4) — continuation persists PAST model-state
  lapse; do not exit when the state dies. 30m hold = capital-efficiency alternative
  (1.23 vs 0.91 bps/capital-minute 2025); 15m dead.
- EXPOSURE: persistence harvest = re-enter after each 60m hold at next qualified minute
  (E3/E6), ~1.6 entries/episode. Cycle-2 keeps full edge (+59.2), cycle-3 dead weight
  (+9.2) but harmless. First-entry-only (E1) is BELOW stream average in 2026 (-2.5).
- ENTRY GATE: rvol>8 at the entry minute (2025: +70.7 vs -49.7 for rvol 4-8).
- FROZEN E6 (rvol>8 gate + unlimited 60m re-entry cycles): 2025 +66.4bps/unit wr .548
  5/5 months, +113.9/episode; 2026 +34.4bps/unit wr .498 3/3 months (+78/+3/+27),
  +54.1/episode, 5.4 entries/day. ALL 3 pre-registered gates PASS on 2026.
  Survives 40bps RT at +14.4 (2026) / +46.4 (2025).
- ECONOMICS (cap 10 names x $10k units): 2026 mean +$194/day (p10 -1,262/p90 +1,400),
  57/61 trade-days, ~4 positions avg, zero entries skipped -> not concurrency-bound at
  small size. 2025 mean +$415/day.
- Feature parity: PAPER_BOT_SPEC.md v0 was WRONG on rvol (real def: cum $vol / 20-session
  bucket-interpolated expected cum $vol), market_ret_5m (real: cross-sectional MEDIAN over
  entire clean universe), excess_gain (gain minus that median). Spec rewritten with exact
  build_features.py definitions. Advisor suspicion confirmed and fixed.
Bot spec updated (PAPER_BOT_SPEC.md): composite gate + E6 exposure + fixed 60m exit.
Next: implement v0 paper bot per spec; live rvol baseline table needs 20 sessions warmup.

## 2026-09-02 (session 3) — rvol corruption + corrected verdicts + frozen v2 + fresh-window prep
- **BUG (fixed)**: build_features.rvol_attach cum_sum over unsorted rows → all 11 feature parquets had row-order-partial-sum rvol. Fixed (sort before cumsum). tests/test_feature_parity.py caught it (live engine == intended math).
- **Rebuilt**: features 2025-05..2026-03 (v2), model_v2.pkl (best_iter 56), theta re-derived dev-only = 0.00098 (theta_hi 0.00259).
- **Corrected verdicts**: composite FROZEN Nov+Dec +86.1bps@20 (was +102.6); E6 2025 +45.0bps/unit (was +66.4); **E6 2026 −62.8bps/unit (was +34.4) — 2026 GO REVERSED**; eval_2026 all cuts negative. RVOL_CORRUPTION_REPORT.md.
- **Trust audit**: tests/audit_features_independent.py — hand math from raw chronological bars == research parquet == live engine; 1253 event rows, 0 mismatches (train/OOS/2026 samples). Sortedness audit of all window ops: only rvol_attach was unsorted; labels/rank/m5 order-independent (self-join/median/rank).
- **FROZEN_V2.md committed BEFORE fresh window** (model_v2 + theta 0.00098 + M3/composite/E6_rvol8/X_60m/cap10/$10k; eval protocol pre-registered; no tuning between months).
- **Fresh window blocked on data**: HF dataset (mito0o852/OHLCV-1m + all mirrors) ends 2026-03; updated 2026-05-03. 2026-04..08 exist nowhere local. FIX: backfill from Alpaca SIP historical (entitlement VERIFIED via test pull). factory/scripts/backfill_alpaca.py → HF-schema raw parquets; clean/certify/featurize chain unchanged; validate_backfill.py (bar-level vs HF 2026-03) gates the run. eval_frozen.py written+committed BEFORE fresh data lands.
- Decision rule (pre-registered): paper-deploy iff pooled net/unit > 0 @20bps AND all five months ≥ −20bps/unit; else no-go + mechanism investigation, no re-tune.

## 2026-09-03 — fresh window built (Alpaca SIP backfill) + NO-GO verdict
- **Data fix**: HF dataset ends 2026-03 → backfilled 2026-03..08 from Alpaca SIP
  (backfill_alpaca.py, resumable, ~36-39M rows/month). Validation on 2026-03 vs HF:
  100% listed-ticker bar coverage, 99.5% closes <0.5%, 99.1% vols <1% → PASS.
  Clean/certify/featurize chain unchanged; features 2026-04..08 coverage healthy
  (rvol .94-.97). NOTE: first featurize pass wrote all-null features (clean files in
  data/backfill/, build_features reads data/) — caught by coverage check, re-run fixed.
- **FROZEN one-shot eval (2026-04..08)**: **NO-GO.** Pooled −41.6bps/unit @20 (−61.6
  @40), wr 0.480, 215 entries (2.9/day), −$115/day, all 5 months negative (−16..−153),
  top-5 = 13.2% |gross| (broad loss). Both pre-registered deploy conditions failed.
- **Post-hoc decomposition** (REPORT_FRESH_WINDOW_2026.md): model IC fresh +0.018 (weak
  but alive, ~1/3 of 2025 val +0.059); M3 flat; the 2025-selected composite gates
  (rvol>4 & vwap>0.03) FLIPPED negative (+0.86% 2025 → −0.13% fresh) — the
  extension/volume conditional is what failed to transfer, not primarily the model.
  E6 within-episode timing −42bps vs pool +9bps (noise-dominated, n=215).
- **Status**: no validated edge in this family as of 2026-08. Paper bot = measurement
  instrument (if built). New hypotheses require new data (2026-09+). No re-tuning on
  04-08.

## 2026-09-03 — forensic investigation (advisor brief + "something is off" intuition)
- **Implementation/data/parity: CLEAN.** 11 checks: hand-math audit, sortedness audit,
  bar-level xvendor (100%/99.5%), FULL-STACK March replication (Alpaca −87.7 vs HF
  −74.6bps, same sign/magnitude), eval_frozen replays 2025 at +45.0 EXACTLY, no April
  vendor cliff (Q1 worse than fresh), checks.json/coverage stable, nulls incident
  fixed pre-eval. Reviewer subagent NOT completed (2x provider 429s) — disclosed.
- **What broke**: 60-min continuation lift fired 2/5 OOS months 2025 (Sep/Dec), ~0/8
  in 2026. L/S quintile spread negative 11/13 months. Only Dec-2025 significant
  (t=+2.27, n=41); 2025 pooled t=+1.40 (ns); 13-month arc −10bps t=−0.53.
- **2025 anatomy**: weak signal + mild selection-adjacency + one lucky month (Dec;
  cycle-3 n=20 +222bps, vwap-extreme n=39 +190bps tails). Repeatable core (cycle-1)
  was +30bps t≈1.2. Market-regime proxies do NOT separate winners/losers.
- **Execution exonerated**: t+0/t+1, holds 15/30/60, cycles, timing — nothing flips
  2026; gross negative so costs aren't it. Negative pool, no rule rescues it.
- **Mechanism dead (long)**: no conditioning works in 2026 (12+ variants); multi-day
  continuation NEVER existed (gainer fade both years); short side ≈ breakeven.
- **Candidate**: NONE. rvol 8-12 same-sign both periods but t≈1.0 post-hoc — not a
  recommendation. Next: forward paper MEASUREMENT only (Sep-Dec 2026, zero capital);
  new hypotheses need new pre-reg. NO paper deployment of this stack.

## 2026-09-03 — Cameron/discretionary-momentum research lane (design only, no implementation)
- Researcher brief obtained (DIRECT/INFERRED labeled; no web fetch available — cutoffs need primary-source confirmation, but our tests grid our own numbers anyway).
- Data availability VERIFIED: yfinance float+short% (~0.7s/ticker); Alpaca NewsClient historical news (timing rule: catalyst window ends at 9:30 ET; movers-roundups are coincident, excluded); premarket bars exist in raw backfill files; trade_count NOT retained (design change: keep it in future backfills).
- Key mechanism probes (16-month panel): HINDSIGHT leader minutes +30/+51bps (2025/2026, incl. mega-leaders +25/+67bps) vs tail −41/−43bps — regime correlates with sign in BOTH years. CAUSAL minute-rank-1: negative every month both years (chasing rotates into tops). Gap = early leader identification = the lane (Stage A prediction + Stage B within-leader patterns).
- RESEARCH_CAMERON_LANE.md: translations table, 7 pattern event specs, 8 design changes (D1 regime-conditioning first ... D8 catalyst A/B), hypotheses H-C1..H-C7 with dev/validation split (2025+2026 = dev; forward paper = validation). Score gate retired to covariate; 60-min fixed hold retired.

## 2026-09-04 — Leader program pass 2 (6 lanes + local; research only, no implementation)
- Web (High): rechecks — gap-fade CONFIRMED (FILTRIX n=66,906); RVOL 2x/5x + 100k PM-vol UNCERTAIN as alpha cutoffs (convention/execution only); float tiers directional, rotation-multiple the real variable; $2-20 tradability compromise (incl. $5-10 worst-fade tier); morning-only + EOD-reversal STRONGLY CONFIRMED. Tradeoff: gross edge first-15-min (unchaseable), net window 09:45-11:00, midday/EOD headwinds.
- Patterns publicly ~0 unconditional; leader-vs-tail test exists nowhere — only our panel can run it. ML verdict: profit-weighted learning-to-rank + calibrated meta-gate, 3-stage funnel; binary leader classification rejected.
- Empirical (21-22 sessions, causal): 10-20% true-gap pocket both years; tod gradient 81/37/18/14 (2025), 46/24/18/5 (2026); top-3-by-15:00 leaders +~48% to close (9/10 beat costs); snapshot #1 holds <=41%, top-5 50-64%; separation@15:00 85.7% vs 12.5% (n=22); share non-monotonic; PM-high distance (~0) + scarcity (~0) DEMOTED. 2025 clean files lack premarket; 2026 backfill has it.
- Doc: researches/07-dominant-leader-program.md (framework v1). No production bot. Next: Stage-A tests 1-7 in doc.

## 2026-09-04 — Leader program pass 3 (advisor consolidation; research only)
- REGIME CAVEAT: causal top-3 fwd flips sign across day-samples (Mar sessions +3.4% both yrs vs spread-year negative vs second 20d +5.7%). Opportunity is regime-clustered, not uniform. No threshold ships without month-blocked proof.
- Representations: diff_pp separation wins (rho .54/.47, 85.7% high-tercile); HHI/share dead; $-vol-only dead (-0.39%, 0% recall). Top-4 gain recall 60% eventual / 95% any-big-mover.
- Stateful: flash-tops below VWAP/off-high vs leaders holding structure; drawdown-from-high corr +.76 with forward; archetypes 7 gap / 0 pure-emergent / 9 other + ELAB late-launch outlier.
- Premarket-backfill GO: 2025 ext-hours ~538MB total, month phases, entitlement OK. Awaiting run approval.
- Target verdict: excursion-gated MFE + recall-first funnel. Doc researches/07 (pass 3). No implementation.

## 2026-09-04 — 2025 premarket repair RUNNING + forward observer BUILT
- Backfill: factory/scripts/backfill_premarket_2025.py (premarket-only <09:30 ET, own
  outputs data/backfill/premarket_ohlcv_2025-MM + premarket_parts/, resume per part).
  API proven, Jan pilot ok (~3-5s/batch, ~40min/mo, ~5GB/yr total, 143GB free).
  Running all 12 months in background (logs/premarket_backfill.log). Existing data untouched.
- Observer: factory/scripts/forward_observe.py (ORDER-FREE by construction + assert +
  test). Session loop 13:25-20:10 UTC, 2-min polls: scans.jsonl (top-30 raw),
  promotions.jsonl (union of top4-gain / gain-x-vol / sep-shortlist + SIP bars/ADV/float/news),
  state.jsonl (rank/sep/$-vol per promoted). Scoring offline — nothing frozen.
  tests: factory/scripts/test_forward_observe.py (5 pass). Dry run ok.
  Live loop started 2026-09-03 22:06 UTC, idles to Fri 13:25 UTC (logs/forward_observe.log).
- Judgment (advisor Q): observer-only first, NO live shadow entries — forward 1-min bars
  make any entry/exit replayable offline with identical SIP data; shadow engine = strategy
  creep. Watch recall-first (2-8 names), precision downstream offline.

## 2026-09-04 — backfill bug postmortem + replay harness + regime wave
- BUG: polars dt.hour()/minute() are Int8 -> `h*60+m` overflowed (10:00 -> 88), so the
  premarket filter kept the COMPLEMENT (evening bars). Caught by hour-dist validation.
  Fix: cast to Int32 + assert zero rows >=14:30 UTC per merged month. Lesson: validate
  distributions, not just row counts. Deleted polluted files, rerunning all 12 months.
- Ops lesson: git-bash `ps` misses Windows python; duplicate background runs raced on
  part files. Use tasklist/wmic + check log coherence (no dup batch lines) for singletons.
- Harness: factory/scripts/replay_watchlist.py — snapshot t -> rules -> E0 (buy-hold,
  selection alpha) vs E1 (first-pullback, timing alpha). Pilot 2d: E0 neg/flat, E1
  positive 70-79% hit (tiny n). One code path for historical + (later) forward-backfilled days.
- Regime cover (web lane lost tooling; ran in main): VIX-gate precedents (Concretum,
  AlgoKing VIX<16/16-24/>24, AIBROKER ERM trend/breadth/dispersion/vol), Daniel-Moskowitz
  panic states, internals (TICK/ADD/VOLD), SmallCapLab next-day, Vortex gap map,
  TradeTheMatrix cycles, FOMO-momentum GH (10yr/23k-ticker negative-results program).
- Observer widened 30->50 live snapshots for future-rule replay; full-market depth via
  next-day historical backfill (DO NOT run backfill_alpaca for 2026-09 until month ends —
  partial final would poison resume-skip).

## 2026-09-04 — pass 4: opportunity-state (n=36d) + leader-state (n=150)
- Day base: top-4 fwd +1.4% mean/-0.1% med; max-fwd +39.8% mean (daily continuation exists).
- HOT TAPE replaces scarcity: n10>=4 -> 61% vs 39% (+2.6 vs +0.2); top1-share NEGATIVE
  (<0.5 -> 67% vs 33%); HHI negative; churn negative. Price-separation good, vol-diffusion good.
- Morning window only: 16:00 emergence not still-tradable. Structure persists, behavior re-dices.
- Names: mid-gain@15 0.58 hit (inverted-U); high-accel fades (0.29); rising>stable;
  2+ pullbacks = left-tail (-7.7% mean, hit holds). Health = mid-gain+decel+rising+near-high+VWAP.
- Regime-web lane failed on tooling; NO-WEB priors filed + main-thread web cover (VIX-gate,
  ERM, panic states, internals). Doc 07 pass 4. No implementation.

## 2026-09-04 — Stage-A harness + block regime map (research only)
- Harness: factory/scripts/stagea_eval.py (baselines cur1/top3-20, sep3, opp gates,
  health lite/full, emerging lane; selected-vs-rejected; multi-block). Fast (~25s/day).
- BLOCKS (causal top-N fwd-to-close, first-sessions/mo + mid-Jun check):
  Mar25 +983 | Apr-May -431 | Jun-Jul cur1 -341 | Aug-Sep top3 -194 | Oct-Nov +274 |
  Dec+Mar26 top3 +1086/cur1 +1903 | mid-Jun -1267 (not month-start artifact).
  Summer = cold regime, fall/winter+Mar = hot. Same rules, opposite signs.
- Early: health-lite/full >> none on hot blocks; emerging lane ~noise so far
  (Aug-Sep -403, Oct-Nov -19, Dec-Mar -5); gates untested (hot days pass all).
- Pending: parity-audit + emerging lanes; backfill Dec; Friday forward session live.

## 2026-09-04 — milestone: Stage-A NOT mature (48d fixed harness)
- Pooled: top3 +17 / top5 +46 / sep3 -39 bps, meds neg, hit ~.45; gates/health move
  nothing pooled; selected-vs-rejected coin flip (20-24/48). Raw watchlist != edge.
- Old block map void (clock artifact); new blocks -507..+831, no seasonal form.
- Survives: ET gradient recheck (4d), hindsight effect (recheck queued), heterogeneity,
  emerging-(a) capped, halt-tolerant gating.
- Assets: 2025 PM repaired+validated (325MB); Friday forward captured (1525 rows/28 syms).
- Next: PM retests, forward scoring, month-blocked state/health proof. No Stage B.

## 2026-09-04 — advisories reconciled; per-name logging; canonical state; orphans moved
- Adopted: target-econ (MFE-gated primary + FP-per-opportunity; mean-fwd retired as ranker);
  inference-audit 15-claim table (5 supported directional, 1 disproven, rest pending);
  repo-debt triage (leave-alone + 3 orphan moves only).
- Built: per-name {fwd,mfe,mae,obp} in stagea --out (was the binding constraint).
- Moved: data/stage_*{md5-verified identical} -> data/_superseded/ + receipt;
  zz_leader_health.csv -> factory/artifacts/; deprecation headers rank_day/extract_events.
- Wrote researches/CANONICAL_STATE.md (fresh-context inheritance contract).
- CodeGraph tested live: 98 files/2.9k nodes, blast-radius + dup-map instant. Adopted as
  structural navigator; no reorg. Note: indexes src/, not factory/scripts/.
- /tmp reaped aggressively — write harness outputs to data/_scratch/, not /tmp.
- Observer redeploy (gain_open_anchored) after Friday close ~20:10 UTC.

## 2026-09-04 — MFE-before-DD confirmed; PM retests; mfe lane timed out (partials kept)
- MFE-before-100bps (33d/165 names, salvaged data/_scratch/mfe_curves.json): pooled
  711/158, P(>200)=0.46; ALL 9 month-blocks positive (298-1678); day-hit 31/33;
  FP/cap 0.8-2.2. Terminal fwd flips sign on same blocks. Target adopted.
- PM retests (20d, n=14,379): echo-70% contradicted blanket (0.56; narrow-PM 0.71 ->
  wide 0.36 monotonic); PM-high distance dead (r=-0.010, permanent demote);
  true gaps weak-negative tilt, tiny-n; PM-$-vol leadership contradicted (0/20).
- mfe-curves lane timed out at 30min despite steer; partial JSON kept. Lesson: cap
  lane scopes to <=24 days or split halves explicitly.

## 2026-09-05 — first forward session scored (n=31 promoted, 26 scored)
- Scorer bugs fixed: str-vs-datetime compare (zeroed all outcomes), month-boundary end,
  empty-stats guard, warrant-symbology variants (5 names genuinely bar-less: BNCWZ,
  EONR.WS, EUDAW, GFAIW, OGGWZ — screener phantoms, 16% of promotions).
- Friday tape: mean fwd -1025 / med -1175 / hit 7/26 / mean MFE +1338. Violent warrant
  tape (BIAFW +9743, TMCWW -7889). MFE>>fwd signature live-confirmed; terminal negative.
- Logging apparatus verified: 178 scans x50, 0 gaps>5min, 2647 state rows, promos
  13:25-19:56, none pre-window. Observer redeployed w/ gain_open_anchored (idle to Mon).

## 2026-09-05 — verification program: rank-1-chase DOWNGRADED, PM retests confirmed
- verify_core.py (new, permanent): A=hindsight (BTAI +17.2/EGG +25.4 fresh, holds);
  B=rank-1 chase h60, 11 monthly points MIXED (means -579..+411) — 'negative every
  month' NOT reproduced; claim downgraded to provisional/horizon-specific. Vectorized
  merge_asof verified exactly vs row-loop (n=328/303, identical means).
- C=PM via harness path (6 fresh days): echo narrow>wide monotonic every day
  (0.54-0.84 vs 0.27-0.39); pmcorr ~0 (one +0.14 day); PM-lead 1/6 (combined 1/26).
- Trust map written to CANONICAL_STATE.md. MFE-magnitude provenance gap noted
  (lane-only code — needs harness-path rerun next).

## 2026-09-05 — interaction scale-up started (per-name E1 + MB detail in harness)
- stagea --out now stores per-name {fwd,mfe,mae,obp,mb100,mb200,e1}. outcome_MB uses
  conservative same-bar rule (DD-first). Convention note: entry-bar included, low-based;
  salvaged lane used looser convention (MSTZ mb100 106 vs 0.0 here) — mine is stricter
  and matches market-order economics. Discrepancy logged, not hidden.
- 3 lanes: rerun-A (03-08), rerun-B (09-12+2026-03, capped scopes), reviewer (audit MB/E1
  wiring + independent mb100 repro spec). Outputs to data/_scratch/sae1_*.json.

## 2026-09-05 — interaction verdict: E1 shows no conditionality; P1s fixed+verified
- E1-first-pullback selected vs rejected: no separation either slice (A +18/0.46 vs
  +9/0.44; B -45/0.32 vs -2/0.42). Pattern #1 fails the attention-conditionality test.
- Fixed: E1 next-bar-open fill, dead-bar ref (et>=t), vectorized MB == loop exactly,
  snapshot cdv vectorized, E1 cached. Post-fix E1 lower (close-fill was optimistic).
- Next: HOD-break + VWAP-reclaim probes before any mechanism rethink.

## 2026-09-05 — E2 (HOD-break) + E3 (VWAP-reclaim) built, scaling
- entries_E2/E3 share _run_trade exit engine with E1 conventions (next-bar fill,
  stop-first, 2R, 15-bar stop, 15:30 ET flatten). Wired e2/e3 into stagea names.
- 3 lanes: rerun-C (03-08), rerun-D (09-12+2026-03), reviewer2 (E2/E3 audit).

## 2026-09-05 — health-conditioned interaction: E3 separates, E1 weak, E2 no
- 44d stored files: E1 kept +48/.51 vs dropped -5/.43; E2 kept -20/.35 vs -56/.37 (no);
  E3 kept +24/.43 vs dropped -48/.26 (separates on both metrics, n=60/77).
- Caveat: files predate session-VWAP fix — E3 verdict needs rerun with true VWAP.
- Fixed: E3 session-open VWAP cumsum; replay_day E2/E3 branches + unknown-name guard;
  duplicate return removed.

## 2026-09-05 — E3-fixed rerun: no consistent separation; E1-health weakly consistent
- E3 true-VWAP kept-vs-dropped: E-lane pooled favors DROPPED (+64 vs +41, 2 reversal
  months May/Aug dominate); F-lane pooled favors kept (+34 vs -151, n=27, 1 reversal +
  2 degenerate months). Verdict: NO consistent separation. Old +24/-48 was VWAP-def artifact.
- E1-health across THREE slices same direction: 44d files +48/-5 (.51/.43); E-lane
  +99/-24 (.60/.39, kept wins 4/6mo); F-lane +37/-9 (.54/.38). Modest (+50-120bps,
  +8-20pp hit) but unanimous. Best-supported claim in program; still small-n.
- Reframing: health value may be FP-avoidance (kept names skip bad setups; trigger-rate
  gaps 0.45/0.28, Oct kept 0/3 E3 fires vs dropped 6/7 losers), not trade improvement.
- Harness gap: no --day-offset (F-lane stuck on first-2-days). Add before full-month runs.

## 2026-09-06 — E1-health DISSOLVED at full-month scale (146d, ~290 triggers)
- Full-A (Mar-Jun 83d): kept-vs-dropped +27.6bps mean, +0.036 hit; capture medians
  negative both; verdict: no separation.
- Full-C (Nov/Dec/Mar26 63d): pooled means indistinguishable (-15 vs -11); capture
  medians ~0 both; verdict: no. Full-B (Jul-Oct) timed out; gap noted, non-blocking.
- E1-first-pullback x health-full: earlier 3-slice weak signal was small-n mirage.
  Per pre-registered tree: mechanism-rethink branch for THIS formulation (gain-rank and
  health-state selection x pullback timing). Opening-shape conditioning UNTESTED.
- Standing: MFE-before-DD target (month-blocked); E1 trigger-suppression asymmetry;
  Friday forward session; repaired PM data; verified harness.

## 2026-09-06 — top-3 path→opportunity program ADOPTED as central lane (design frozen)
- 3 advisory lanes reconciled (ml-formulation / data-inventory / red-team). Verdict: GO
  as bounded falsification probe with pre-registered kill criteria; prior for success low.
- Formulation: clustering K=3-6 + Ridge <=10-15 vars; stock-day unit (episodes as features
  only, day-clustered SEs); mb200_capped primary (+mb100 comparability); support gates
  (>=15-20 obs, 3+ months, applicability check); month-blocked validation; 2 unseen months
  required (1 month = pilot only). Sequence models rejected; GBM capped challenger; kNN diagnostic.
- Dev = 2025-05+06+07 (63 contiguous days, mixed regimes, PM-capable); collision = 2025-08
  then 2025-10. Reserve 2025-03/04 + all 2026. Arms A (raw) / B ($2-20+liq) separate models.
- Preconditions (wiring): premarket branch fix (dead code today) + prev-close/split join
  into snapshot + RVOL baselines for dev months. Turnover dropped (no PIT float); halt proxy
  = bar absence; late-starters flagged.
- Kill criteria (binding): DEAD if d_hit<=+5pp or median-capture<=0 or degenerate rates;
  WEAK if +5-15pp both arms CI-incl-0 -> expand 2 months frozen; PROMISING only if
  d_hit>=+15-20pp AND capture>=+100-150bps AND FP<1.0, both arms, n>=60, search disclosed.

## 2026-09-06 — reviewer P0s fixed before any learning (in_B peek, DTW inf)
- in_B now snapshot-time price (was full-day close = 6h peek). ret_last30 (dup of
  ret_open_T) replaced by range_position; z_len explicit (halt proxy, not hidden);
  DTW full-window + length-normalized (unequal lengths finite, order-sensitive);
  PM frame guarded et<570 inside builder. Tests 10 pass incl. new DTW/uniqueness.
- Dev panels (May/Jun/Jul) + Aug holdout must be REBUILT (in_B + 29-feature schema).

## 2026-09-06 — August collision: Ridge dead, clustering weakly alive
- Freeze (May-Jul dev): Ridge LOMO rank-IC ~0 both arms (alpha maxed = shrunk to nothing).
- Collision Aug (63 territ): rankIC -0.13/-0.23, top-1 model loses to gain baseline
  (393 vs 444; 359 vs 402). Supervised linear carries nothing. Dead as specified.
- DTW-medoid cluster ORDER roughly replicates: A dev C0<C2<C1<C3 -> test C2<C0<C1<C3
  (best 602, n=8); B dev C1<C2<C3<C0 -> test C2<C1<C3<C0 (best 606, n=5). Weak,
  underpowered, same direction. Alive-but-weak: October collision decides.
- All pre-registered: K=4, support gates, mb200_capped, both arms, disclosed search.

## 2026-09-06 — October collision PRE-REGISTERED (before seeing October)
- Verdict metric (frozen): Spearman(dev cluster MEANS vs October cluster MEDIANS),
  plus dev-best cluster finishes top-two in October. Computed once, blind.
- PASS: rho >= +0.6 with dev-best top-two. FAIL: below, or ordering scrambles.
- August reference: rho=+0.80 both arms (means AND medians). October decides whether
  shape recurrence is real (2 months) or an small-n alignment.

## 2026-09-06 — October collision: FAIL. Representation retired.
- Pre-registered bar (rho>=0.6 + dev-best top-two): arm A rho=-0.40, dev-best C3
  finishes 4/4 (med 15, worst); arm B rho=+0.20, dev-best 3/4. FAIL both arms.
- Ridge dead again (rankIC +0.04/-0.14). August 0.80 = small-n alignment, retired
  with E1-health into the mirage file.
- Note: Oct arm-B only 39 rows (top-3s often outside $2-20 — penny-heavy month).
- Standing per tree: gain-rank/health/E1-E3/shape-medoid formulations ALL failed.
  Surviving: MFE target, harness, PM data, Friday forward loop, apparatus.
  Next: rethink branch — finer representations, turnover-gating, regime-conditioning,
  or thesis revision. No new learning runs until the rethink lands.

## 2026-09-06 — hypothesis round: 5 thinking lanes -> HYPOTHESES.md (living doc)
- H1 scanner-lag rental, H2 participation/narrow-base, H3 turnover-clock, H4 veto,
  H5 halt-resolution, H6 fade, H7 transitions/legs. Anomaly ledger (7). Kill criteria
  binding. Adversarial standing kept open with flip conditions both sides.
- Test queue ordered zero-new-data-first: lag buckets, frozen participation, veto.

## 2026-09-07 — runner phenomenology + thrust+halt gate (first positive month-blocked result)
- Built runner_phenom.py; scanned 172 days (2025-05..12, 2026-03): 141 runners ≥60%.
  Phenomenology: move takes all day (med 322min open→high, 55% of move after noon),
  halts monotone with gain (84% halt; 11+ halts → mean +177%), retrace doesn't kill.
- FP check: gate = ≥15% by 10:30 + ≥1 halt-gap → rest-of-day from 10:30 next-bar-open:
  n=281, fwd mean +2.8% vs ctrl +0.3%, 7/9 months positive, MFE med +11% after entry.
  First positive month-blocked cohort result in project history. Payoff is tail-shaped
  (win 48%); extraction = stay-rules problem (H2) on a visible gate (H5 fingerprint).
- Artifacts: factory/artifacts/runner_phenom_2025.json, sig_halt_1030.json.
- Misdiagnosis log (mine): month scans died to `timeout 280` before writing; I blamed
  session-reaping for 2 cycles. Fixed: per-day incremental JSONL + resume. Lesson: write
  incrementally, verify the artifact exists before reasoning.

## 2026-09-07b — gate decomposition + halt-reopen mechanics
- Decomposed the +2.8% gate: thrust alone dead (+0.3%), interaction carries it
  (15-30%x1halt +5.3%). >=30%+1halt is NEGATIVE (-6.8%) — exhaustion zone.
- 22,739 live halt events: average reopen ≈ 0 (scalp dead), but velocity≥30% &
  hole≥11min → ret3 +3.9% win 57%, 8/9 months. Halt-ordinal decays; morning only.
- New H9: event-clock participation machine (state coords, not wall clock).
  Artifacts: gate_decomp_2025.json, halt_reopen_all.json (committed).

## 2026-09-07c — temporal decomposition: anchor illusion resolved
- The +3.9% post-halt continuation was pre-halt->reopen GAP (anchor illusion); causal
  next-open entry after reopen is NEGATIVE (D30 -1.5% morning). Reopen buying dead.
- Already-long is where gap money accrues: morning qualifying halt gaps +6.3% med +4.4%.
- Gate cohort accrual schedule: +1.2% by 11:00, dead 11-14, +2.0% in 14-16 wave.
  H10a hold-through-halt / H10b afternoon rental pre-registered. Artifacts committed.

## 2026-09-07d — capture pricing → H11 afternoon rental
- Priced dumb hold (+1.24%), abort-7% (worse: +0.43%), 13:30 rental (+2.39% best).
  Green@13:30 dead on general pop (+0.24%); gate interaction is load-bearing.
- Tail-concentration honest: excl top-5 mean +1.07%; 2nd half (Nov-Mar) +0.67%
  (excl top-3 −0.38%), 2026-03 alone +2.19%. Lottery-book anatomy.
- H11 = stupid-simple afternoon rental of gate names, pre-registered kill on two
  flat collision months. Artifacts: gate_pm_full.json, pm_green_all.json.

## 2026-09-08 — H11 collision test: FAILED, hypothesis retired
- Ran frozen rule on 4 unseen months (2025-03/04, 2026-01/02): pooled net -2.13%
  (gross -1.13% — sign flip pre-friction), 2025-04 -6.27%, 2026-01 -3.51%.
- Pre-registered kill met (two consecutive negative collision months). H11 retired.
- Meta-finding (4th dev-positive to die on collision): month-blocked dev positives
  with n~20-30/month do not replicate; power is the binding constraint.
- Artifact: factory/artifacts/h11_collision_results.json. Halt/runner phenomenology
  stands as description; tradability unproven for every formulation tried so far.

## 2026-09-08 — project reorganization (organization session)
- 6-lane recon (history/data/scripts/srcdiff/contradicts/git) + fresh-eyes review.
- Built brain: researches/INTENT.md (stable objective) + researches/STATE.md (current
  truth snapshot) + entry AGENTS.md (read-order); HYPOTHESES.md archived A-E layers.
- Audit corrections applied: runner freq 0.82/day (was 1.2-1.5, inflated by day-#1
  fallback), halt-gap mean/median disentangled (+6.3 mean/+4.4 med), gate-cohort
  qualifier on money-map, sig(281 rows/118d) vs gd(245/96d) day-coverage resolution,
  281-vs-245 not-a-trend, halt_reopen 1.06M rows, rank-1 chasing conflict recorded
  (minute-claim vs h60-mixed — unresolved), CANONICAL_STATE + 07 frozen with headers.
- Provenance doctrine: producer scripts committed; day-coverage reported per cohort;
  friction annotated per claim. Ledgers backfilled H011-022, EXP-20..34.
- Git: 55 commits ahead of origin (clean FF push, awaiting user); untracked
  certification dirs + ml coverage still uncommitted (next: commit evidence);
  src/ workstream (~3.1k lines, Phase A/B/C paper-bot) uncommitted, reviewed, split
  recommended before commit; data/ gitignored by policy.

## 2026-09-08b — full file mapping round (6 lanes) + repairs
- Verdicts recorded in researches/STATE.md file-map doctrine. June audits 00-06 ->
  researches/history/. Clean 2026-04..08 un-orphaned (moved to backfill/, load_day
  verified 2026-04+2026-08 OK) — P2 pooled re-measure now unblocked. Stale
  data/clean_2026-03 dup deleted. h007/h010 c10/c20 dup parquets pruned; _scratch
  dom studies pruned. Bot workstream committed (Phase A/B/C, SPEC 11.19); ml logs
  gitignored. Working tree clean; 70 commits ahead of origin pending push.

## 2026-09-08 — forward observer running supervised
- Observer live via factory/scripts/observe_supervisor.sh (auto-restart loop, 5s);
  single tree, failure-safe. Window 13:25-20:10 UTC today. Scans -> data/forward/.
- Infra lesson: inspect Windows processes with WMI filters excluding own commands;
  Stop-Process must be tree-kill (taskkill /T), else supervisors resurrect children.

## 2026-09-08c — PRE-REG-P2 frozen (sequential step 1)
- Wrote researches/PRE-REG-P2.md: 13-month dev pool (2025-03..2026-03), sealed
  2026-04..08 holdout (with ML-lineage overlap disclosed), frozen gate cohort,
  fixed M1-M6 metrics, three decision gates. No edits allowed; new file for changes.
- Next sequential: check/score 2026-09-07 forward day.

## 2026-09-08d — forward-day checks + scorer verified
- 2026-09-07 (Mon) NOT captured (observer only launched today) — coverage starts 2026-09-08. P1 ops gap noted.
- score_forward_day.py dry-run on 2026-09-04: reproduces scores.json byte-identically (31 rows). Tonight's pipeline green. Needed: pip install alpaca-py (done) + .env keys present.

## 2026-09-08e — observer outage + recovery (honest ops log)
- 13:25-15:05 UTC: observer alive but every poll crashed (No module named 'loguru').
  Detached supervisor's python != shell python env. ~100 min of today's tape LOST.
- Fixed by pip install loguru (+yfinance for float enrichment) into hermes venv;
  running process self-healed (failed imports aren't cached). First good poll
  15:05:17 UTC (cands=50, watch=5). Supervisor hardened: pinned interpreter path
  + dep preflight check (loud MISSING DEPS instead of silent poll-error loop).
- Running loop still holds old script body (bash parses while-loop upfront); new
  body activates on next supervisor restart. No mid-session restart (would duplicate
  today's promotion rows). SIP snapshot fields null on scans (same as 09-04 — known;
  deep snapshots pull bars separately).

## 2026-09-08f — P2 verification closed (reviewer + verdict script)
- Reviewer audit (static): producer faithful to pre-reg cohort; halt counting,
  RTH-only holes, entry anchors all match. No blocking bugs.
- Closed provenance gap: factory/scripts/moneymap_verdict.py committed — reproduces
  every M1-M6 number from the artifacts exactly. Morning-D30 "-1.54% exact" flag
  resolved: rounding coincidence across different day-coverage samples (dev subset
  in-scan -1.73% n=37; pooled -1.54% n=50). Honest note in HYPOTHESES.
- Known P3-relevant caveats (cosmetic for P2 description): halt spanning 10:30 ->
  p0 measured post-gap; halt spanning 13:30 -> p1330 pre-halt close (upward bias on
  gap days); late-day (et>930) halt events dropped from M1; hourly path drops
  inter-bucket 1-min moves. P3 entry design must handle all four explicitly.

## 2026-09-09a — H12 hot-window pattern-mining: BUILT, RUN, DEAD (pre-registered)
- New lane from user directive: causal top-5 gainers (prev-close gain, scanner
  semantics), sampled 9:45-10:30 ET every 5 min, 22 raw 1-min OHLCV features,
  LightGBM target = first-touch +4% before -2% in 30 min, next-bar-open entry.
- Pre-registered BEFORE any run: researches/PRE-REG-H12.md (gates: dev LOMO AUC
  >=0.55 AND top-decile net >= +30bps; collision AUC >=0.53 AND net >= +30bps AND
  positive in >=2/3 months; 100bps friction).
- Producer: factory/scripts/hot_window_ml.py (imports harness snapshot semantics;
  sample cache data/cache_h12/ per-day parquet, resumable; H12_STAGE=/tmp staging
  because /mnt/c parquet reads are ~10x slower on ext4). Data: 273 days,
  13 months (2025-03..2026-03), n=9074 dev + 2793 collision samples.
- Verdict: DEV GATE FAIL (AUC 0.60 pass, top-decile net -50bps fail — rank info
  without tradable drift), COLLISION FAIL (AUC 0.565 but deciles flat/inverted,
  top decile worst at -285bps, 3/3 months negative, dedup -187bps). H12 RETIRED.
  2026-04..08 still sealed. Artifact: factory/artifacts/hot_window_ml_H12_dev.json.
- Env notes for future long jobs: WSL OOM kills came from et_minute's dt.date
  object-array on 23M-row months (fixed via tz_convert path in hot_window_ml);
  bash-tool detached processes get reaped between calls — run long caches in
  ~14-min chunks with per-day incremental writes (resume by design) instead.

## 2026-09-09b — H12 SCOPE CORRECTION + post-mortem probe (lane open, probe dead)
- User directive: H12-as-tested (one probe) may be dead; the thesis "top gainers,
  hot window, price action -> recurring money-patterns" is NOT refuted by it.
  Discovery mode stays primary; validation hardens only when something looks
  monetizable. Recorded in HYPOTHESES.md H12 SCOPE CORRECTION.
- Probe (hot_window_ml.py probe, artifact hot_window_ml_H12_probe.json):
  (1) T1-matched bracket exit (+400/-200/30min) dead every month, dev AND
  collision (-156..-175 net dec8+); excursion-not-terminal story refuted —
  model's top scores = dn-first states (60-64%). (2) Surviving OOS AUC 0.565 =
  vol-classification (volrank control 0.572); dev up-specific lift gone in 2026.
  (3) Within model's top picks no raw scalar separates up_first (max 0.561).
- Consequence: scalar-summary representation is exhausted on this population.
  Next probe must change REPRESENTATION (raw path) or ECONOMIC QUESTION, not
  re-tune scalars. No new lane opened yet — decision with user.

## 2026-09-09c — bracket bug FIXED, probe numbers corrected (verdict unchanged)
- User caught real ordering bug in bracket_ret: mae-first check charged -200 to
  up-first-then-later-break trades. Fixed to use T1 ordering (T1=1 -> +400 flat).
- Corrected numbers: dev dec9 bracket gross +33 (was -55), dedup -80 (n=851);
  collision dec8 gross +18 vs T2 -97 (partial rescue of terminal fade is real),
  dec9 gross -2, dedup -89 (n=245). Dev dec8+ months net -46..-102, collision
  -87..-96. Short mirror no longer computed (bug made it meaningless).
- Broad verdict unchanged: matched-exit dead at 100bps everywhere, no month
  clears +30 net. But "model picks = dn-first states" was overstated — top picks
  carry REAL up-first-then-fade rescue relative to terminal, gross magnitude
  just 1/3 of friction. Vol-classification finding (#2) and scalar-exhaustion
  finding (#3) unchanged. Artifacts + H12 SCOPE CORRECTION revised in place.

## 2026-09-09d — H12 PATH PROBE (the literal question): raw path adds nothing OOS
- Representation: last 30 bars right-aligned, 4 scale-free channels/bar (bar
  ret, range, vol-ratio, close-position) = 120 features; scalars / path / both,
  same fixed LightGBM, same LOMO dev 2025-03..12 + collision 2026-01..03.
- Result: dev AUC 0.601 / 0.599 / 0.610 — path alone matches scalars, +path adds
  ~1pt in dev. COLLISION: 0.565 / 0.550 / 0.557 — path does NOT add OOS; path-
  only WORSE than scalars. Bracket economics still negative everywhere (dev
  dec9 -56..-67, collision -102..-129). Early-window (t<=600) dev path lift
  (0.584 vs scalars 0.568) did not survive collision.
- Combined with EXP-38 (relative within-snapshot vol-explained OOS): on this
  population at 30-min first-touch horizon, neither scalar summaries NOR raw
  bar-path of the last 30 bars carry OOS direction information beyond
  volatility. The lane's remaining untested directions: different horizons
  (shorter than 30 min / multi-leg), conditioning objects (halt events, catalyst
  context), or finer tape. Sequence-shape per se on 1-min bars: tested, dead.
- Producer: factory/scripts/hot_window_path.py; artifact:
  factory/artifacts/hot_window_ml_H12_path.json (+ relative probe
  hot_window_ml_H12_relative.json).

## 2026-09-09e — leaderboard (TV-equivalent) rebuilt, contract enforced
- User stopped hist runs mid-way: three defects fixed in tv_leaderboard.py.
  (1) prev_close_map ranked mid-month days vs the PRIOR MONTH's last close —
  now searches back by session date for the IMMEDIATELY prior session (April+
  caches were garbage; purged, 52 files). (2) Lag was double-staled
  (shift(1)+et<=t-1 => bar t-2); now causal px = close of last bar et<=t-1
  directly. (3) Grid no longer baked: --ts arbitrary; default 585..630/5 only
  when caller passes nothing.
- Verified with explicit traces (verify cmd): 2025-04-07 t=585/600/615
  (BJDX +138/+122/+159% vs prev_close 3.53 — cross-checked vs raw 2025-04-04
  bars) and 2025-07-08 t=592/603 (NDRA +171/+159%, arbitrary t works).
- Contract written into researches/STATE.md (Leaderboard contract section).
  Live source = scanner API (no key). Leaderboard ONLY — no sampling/targets/
  horizons chosen. Research design deferred to user.

## 2026-09-09f — leaderboard preprocessing FINISHED (grid gone, PIT universe)
- --ts mandatory everywhere (hist/verify error without it; the 585..630/5
  H12 grid is deleted from the codebase, not just defaulted).
- PIT check: universe_tags.parquet = single CURRENT yfinance snapshot, NOT
  point-in-time (no date dimension). Real PIT source per factory/AGENTS.md:
  yolo22/stock-pit-archives. Downloaded the 492MB US-Stock-Symbols bundle,
  extracted 369 daily vintages x (nasdaq|nyse|amex) symbol lists with junk-name
  exclusion (warrant/right/unit/preferred/note) at extract time ->
  data/pit/pit_symbols.parquet (2.1M rows, 2.5MB). Survivorship verified:
  845 delistings + 940 new listings across 2025-03..2026-08.
- eligible_set(day) now = PIT symbols as of that date. First catch: JNVR,
  the TRUE #1 gainer (+249% at 9:45) on 2025-04-07, was invisible to the old
  yfinance tags (NaN exchange / NONE quote type). Old tag filter also
  survivorship-biased in reverse (kept later-delisted names as eligible via
  stale rows). Leaderboard cache purged again; rebuilds use PIT.
- Live fetcher unchanged (scanner API is inherently current-date PIT).

## 2026-09-09g — 18-month 1-minute leaderboard + paths COMPLETE (EXP-42)
- factory/scripts/lb18.py vectorized: per day, per RTH minute t=571..959,
  causal top-3 by gain vs immediately prior session close, PIT universe;
  top-3 rows -> lb_YYYY-MM-DD.parquet; union-of-top3 causal paths (o/h/l/c/v,
  gain_c, n_bars at last completed bar) -> path_YYYY-MM-DD.parquet.
- Built 2025-03..2026-08: 377 days, 756 files, 41MB, 146k minute-samples.
- Correctness: vectorized version verified byte-equal vs earlier per-ticker
  implementation on all of April + JNVR(2025-04-07)/SLBT(2026-06-16) traces.
  Found + fixed a 1-bar lookahead in the first script version affecting only
  2025-03-04..07 (rebuilt). Causality contract: bar t-1 completes at t.
- Storage well under budget (41MB vs 30GB constraint).

## 2026-09-09h — 18-month causal top-3 discovery arc (raw path mining)
- Built 2025-03..2026-08 leaderboards+paths at every RTH minute (377 days,
  756 files, 41MB) via factory/scripts/lb18.py (vectorized per-month wide
  matrices; verified byte-equal against independent tv_leaderboard.py traces).
- Leak rule established: path files include union-of-top3 names; analyses must
  exclude future_hot rows (names whose first top-3 appearance is after t).
- Discovery scripts (all causal, month-blocked): lb18_clusters/events/stability/
  moments/leakcheck/causal/state/dip.py; artifacts lb18_*.json.
- HEADLINE PHENOMENON: flush-recovery in extreme states. In g100+/fresh/thrust
  top-3 minutes: 70.5% see >=10% flush within 2h; of those 56.2% recover to
  breakeven (median 4 min to recovery), 52.3% recover before deeper, 21.3% get
  a new +20% leg off the low; 18/18 months positive recovery rate.
- Emerging formulation (NOT yet pre-registered): bid the flush in extreme
  fresh+thrust leaders, target breakeven recovery — weakness, not strength.
- Next: mechanical pre-registered test of that formulation with costs, plus
  flush-depth/timing conditioning. Memory: researches/HYPOTHESES.md 2026-09-09g.

## 2026-09-09i — flush conditioning (lb18_flush2)
- 1079 flush events: fast (1078/1079 <=3 bars), morning best (rec 0.688),
  rank1 0.587, 300%+ 0.597, -15..-25% depth newx 0.296. Ultra-deep (<-25%)
  rec 0.30. Artifact lb18_flush2.json. Formulation crystallizing:
  resting bid into fast flushes of extreme fresh+thrust leaders, hot-window,
  rank-1 bias, flush depth -10..-25%, target breakeven-recovery + runner tail.

## 2026-09-09j — flush context + fill realism (lb18_flush3)
- 1084 flushes of 1537 state moments (70.5% incidence). Fill realism: 17.4% gap
  through the -10% bid; median overshoot past bid -2.3% (q75 -0.85%); 55.5% of
  flush bars close back above the bid.
- Race (win = recover to c0, stop = -10% from fill): p_win 0.484 (clean 0.480),
  mean win +12.4%, descriptive EV +0.87% gross (~-0.13% at 100bps). Aggregate race
  is a coin flip, net-negative at doctrine friction.
- Conditioning that helps rec_first: morning rank-1 0.592 (6/8 months), thrust15
  20%+ 0.577 (newx 0.283), gain 300-600% 0.553, nbar<20 0.552 (newx 0.315, also
  deepest 0.776). First flush of day NOT bought (0.136, n=44). Depth 15-25% recovers
  worse (0.407) but fatter new leg (0.294). rank2 weakest (0.380).
- Descriptive net-of-100bps on best cells ~+1.9..+2.3% (small n) — needs a frozen
  pre-registered test, not belief.
- Artifact lb18_flush3.json; producer lb18_flush3.py.

## 2026-09-09k — flush measurement repair (reviewer audit) + corrected episode read
- lb18_episodes.py (new producer, lean): touches/volume/speed use real NEW bars
  (n_bars increments) only; native 1-min state minutes (no t%5); flush events
  deduped by (date,ticker,fill-minute) with multiplicity; speed index bug fixed;
  prior_flush counts distinct episodes; per-event race with fill-bar ordering
  bounds (pess/opt), EOD censoring flag, gap-through sensitivity; labels fixed
  (x20 vs pre-flush c0; bars_so_far session count).
- Corrected: 7840 native state minutes -> 5294 filled -> 1893 UNIQUE flush events
  (multiplicity mean 2.8/max 21). Fill realism: gap-through 6.97% (not 17.4%),
  overshoot med -1.35%. Old flush3 numbers were snapshot-inflated + ffill-fake.
- Race per event (censored excluded, n=1663): win pess 0.428/opt 0.521; pay
  pess_net -0.75%/opt +1.18%; abort-on-weak-close variant pess -0.06%/opt +0.88%.
- Survivors: fill-bar close>bid (pess +1.67%/opt +3.28%, x20 19.8%); gain 300-600%
  & depth<=15% (n=267, pess +1.04%/opt +3.22%, 13/18 months pess+, fmax q90 +60%);
  repeat-flush > first-flush. Morning edge shrank (pess negative) — revised down.
- Artifact lb18_episodes.json + lb18_episodes_events.parquet; EXP-46 recorded.

## 2026-09-09l — lifecycle + runner capture (scale-out discovery)
- Built lb18_lifecycle.py: one-order-at-a-time lifecycle (bid -10% under decision
  close, rests 120m, no mid-rest replace; fill on first NEW bar; stay/abort at
  fill-bar close; re-arm at next state minute after resolution). 744 orders,
  541 fills, 203 no-fills. Strong fills (close>=bid) 271; weak 270.
- Exit study (lb18_runners.py, lb18_scaleout.py) across all 1663 unique events:
  plain trail20 mean +0.71%; **scale-out (sell half at pre-flush c0, trail
  remainder 15%, floor -10%) mean +2.56%, med +3.06%, 15/18 months positive**.
  Strong subset (fill-close>=+2%): mean +5.86%, med +4.40%, 77% pos, 17-18/18
  months; c0 hit rate 77%. Weak subset: ~breakeven (was -3.8%).
- Tradeable lifecycle population (n=489): so0.50_t0.15 mean +1.04%, med +2.55%,
  11/18 months positive. Positive but thin — needs freeze + fresh months.
- L=0.15 bid depth variant worse on aggregate; keep L=0.10.
- Artifacts: lb18_lifecycle.json/.parquet, lb18_runners.parquet,
  lb18_scaleout.parquet (+grid), lb18_absorb_all.parquet, lb18_runner_variants.parquet.
- Discovery-grade only. Next: freeze the rule + pre-register on untouched months.

## 2026-09-09p — canonical reconciliation + rolling lifecycle (discovery executed)
- Rebuilt all exits in ONE engine (lb18_canon.py) after finding S/L sim mismatch.
  CORRECTION: sell-all-at-c0 (f100) beat partial scale-out; scale-out headline
  (+2.56%) was an artifact of inconsistent sims. Strong fills +5.29%/18of18 (f100);
  tl30 hold-30min improves to +5.38%/18of18. LOCK variants destroy edge. Cutting
  weak fills loses to holding all.
- Lifecycle re-arm diagnosed (lb18_relax.py): blocking re-arm (strict) misses
  +6.73% seq-only fills; rolling refresh (lb18_roll.py: -10% bid refreshed every
  state minute while flat) is the correct executable version: all +0.39%/11of18,
  pf>=2 +1.20%/15of18 worst -1.17%, n=658/18mo (~35 fills/mo).
- Stay/abort by fill-bar close loses under honest exits; abort@bid assumption
  rejected. Seq upper bound (no pyramid): +2.23%/16of18.
- Artifacts: lb18_canon*, canon_*.parquet, lb18_relax*, lb18_roll*, plus prior
  runnerpath/runnermanage. Scripts: lb18_canon.py, lb18_relax.py, lb18_roll.py.
- Status: discovery-grade on all seen months; freeze + forward test is the next step.

## 2026-09-11 — FLUSH RULE OOS PASS (pre-registered; first true OOS pass)
- PRE-REG-FLUSH-01 frozen/committed (33e2943) before any OOS computation; OOS
  runner parity-verified exact on dev (8c8f3d2) before touching OOS months.
- Built 2024-01..2025-02 (14 months) through the unchanged pipeline; PIT
  vintages extended 2023-11..2025-02 from yolo22/stock-pit-archives mirror
  (data/pit/pit_symbols.parquet now 3.94M rows, 2023-11..2026-08).
- OOS result: pf>=2 n=381 +1.14%/trade net, 12/14 months positive, worst month
  -2.03% -> PRE-REG GATE PASS (dev +1.20%/15of18). all +0.91%/11of14.
- Caveats: paper sim; fills-at-bid assumption; halt/gap tails (worst OOS fill
  -22.9%); capacity/execution unproven. Next: forward observer + execution design.
- Artifacts: lb18_oos_oos.json/.parquet (OOS), lb18_oos_dev.json (parity).

## 2026-09-11b — execution realism + live confirmation wiring
- lb18_exec.py on pooled dev+OOS frozen-rule fills (n=1478): clean-touch +1.27%
  (n=1363) vs gap-through -7.64% (n=115, 7.8%); halt-gap share 0.0% (tail is
  collapse, not missing bars); stop breaches 9.1% (worst -35.9%); capacity
  median $3.45M fill-bar volume (5% participation -> $173k); decay all-fills
  half1 +1.08% -> half2 -0.05%, pf2 +1.31% -> +0.96%; tl30 and stop -10..-15
  on broad plateaus (robustness only; rule unchanged).
- Live confirmation wired: forward_observe.py logs session bars for promoted
  names (log_new_bars); flush_forward_score.py replays the frozen engine
  (lb18_oos.run_engine) on those logs. lb18_oos refactored; dev parity
  re-verified exact. Smoke-tested via Windows venv; activates at next observer
  restart (no mid-session restart performed).
- Artifacts: lb18_exec.json/.parquet.

## 2026-09-11c — live confirmation pipeline + first replay
- forward_backfill_bars.py: Alpaca SIP 1-min session bars for live scan days
  (09-04: 158 names/23.7k bars; 09-08: 87/14.4k; 09-09: 231/34k).
- flush_forward_score.py unit bug fixed (percent vs fraction gain); first
  corrected live replay = 1 qualifying fill (FTFT +10.1%), 0 on the other two
  sessions. n far too small; live candidate net is 50-name alpaca_movers.
- Continuous bars logging starts on next observer/supervisor restart.
- NOTE: Alpaca paper API keys appeared in a tool output during recon — rotate.

## 2026-09-11d — fill-realism microstructure evidence (SIP)
- lb18_fills_micro.py: per frozen-rule fill, SIP trades at/below resting bid +
  NBBO snapshot. OOS n=541: touch 98.9%, through 98.3%, at-bid vol med 52.6k sh,
  dwell med 15.3s (strong fills 4.5s/18.9k). Gap fills 2.6x volume (adverse
  selection real). Fill assumption evidence-backed at small size; queue position
  unmeasured until paper/live.

## 2026-09-11e — flush bot + observer supervisors LIVE (dry-run)
- flush_bot.py committed 45acbde; flush_bot_supervisor.sh launched from WSL via
  cmd.exe /c start "flush bot" /min git-bash -lc "bash .../flush_bot_supervisor.sh"
  (the start title MUST contain a space or WSL interop strips quotes and cmd errors).
- Running since 07:56 UTC; polls every 60s; dry-run default (no orders); scans/bids from
  09:30 ET; enable paper orders by creating data/forward/bot/LIVE
  (supervisor relaunches with --live within 5s).
- Observer supervisor restarted too (07:59 UTC) -> bars logging + scans resume; previous
  observer had been down since 2026-09-08.
- Bot: TV scanner candidates; Alpaca SIP bars; broker-truth reconciliation; journal at
  data/forward/bot/<ET-day>/journal.jsonl; guards POS_MAX=3, $2k qty, 15:30 cutoff,
  15:55 flatten, KILL file.

## 2026-09-11f — flush-bot parity tooling + restart procedure
- flush_bot.py now journals scan_row (rank/symbol/close/change) for the top-5 each poll;
  required to replay the bot's own decisions.
- flush_bot_parity.py: reads data/forward/bot/<day>/journal.jsonl, rebuilds
  observer-schema scans/bars in a temp dir, runs the frozen engine (lb18_oos.run_engine),
  diffs expected vs journal fills (symbol +-5 min). Verified on a fabricated 2026-09-09
  journal built from observer scans: engine reproduced the known live fill exactly
  (FTFT tf=678 = 11:18 ET, +10.11%).
- Restart semantics: the supervisor restarts the bot ONLY on process exit; the bot loops
  while True. To reload code or flip --live: touch data/KILL (bot exits within ~60s),
  rm data/KILL, supervisor relaunches within 5s. Used to load scan_row journaling; bot
  now runs the new code, dry-run, market closed.

## 2026-09-11g — flush-bot pre-open hardening (exit tracking + bid concurrency)
Audit of the live order paths found four defects, all fixed before market open:
1. Exits were never detected: when the OCO sold, meta kept entry_bars -> no exit journalled,
   re-arm gate never armed, and a later re-entry on the same symbol SKIPPED its protective
   OCO (fill branch required entry_bars is None). Fix: each poll, entry_bars set but no
   broker position -> journal 'exit', set last_exit_bars, clear entry/oco/order state.
2. Buy-order concurrency: POS_MAX capped positions but not resting bids (up to 10 bids for 3
   slots). Fix: len(positions)+len(buys) >= POS_MAX.
3. tl30 exit and EOD flatten never cleared/reset state (last_exit_bars, entry_*, oco_id).
   Fix: both paths clear state and set last_exit_bars.
4. OCO id was logged but not stored; micro guard for missing filled_at.
Bot restarted via KILL switch to run the hardened code (dry-run, live:false).

## 2026-09-11h — first live paper session armed
- Pre-open: bot on hardened code; LIVE flag set (start 04:23:51 ET, live:true) -> it will
  paper-trade unattended from 09:30 ET. Revert to dry-run: rm data/forward/bot/LIVE then
  KILL-restart (touch data/KILL, rm, supervisor relaunches within 5s).
- Observer supervisor running; paper account clean (0 positions/orders) at arm time.
- After the session: run flush_bot_parity.py --day <day> and inspect the journal
  (scans, bids, fills, OCO hits, tl30 exits, micro stats).

## 2026-09-11i — paper-fill ledger built (account hygiene catch)
- flush_bot_ledger.py: per-trade P&L from Alpaca paper orders (get_orders;
  get_account_activities does not exist in this alpaca-py) joined to the bot
  journal's intended B/c0; artifact factory/artifacts/flush_bot_ledger.json.
- Hygiene catch: the paper account holds 54 foreign fills / 33 trades from
  2026-06-09, 06-11 and 07-14..17 (RNAC/BMNG/BMNU/BATL; mean -4.07%) — not ours
  (bot armed 04:23 ET today, market closed). Ledger filters fills to symbols
  present in the journal on the same date and reports n_foreign_ignored.
  Pre-session truth for our bot: fills=0, trades=0.

## 2026-09-11j — flush bot hardening v2 (audit fixes) + restart
Independent audit (16 blocker + 8 major). Triage: fixed the order/lifecycle
correctness set; deferred 8 parity/ledger (analysis-side) findings.
Fixes: lifecycle managed for all tracked symbols every poll (independent of
scanner membership); ownership via client_order_id prefix (flushbot-);
fills detected by order-id lookup (filled orders leave the open book) with
partial-fill cancel + protect; OCO protection retried until accepted; anchor
and 120-min expiry refreshed at every strict-state minute, broker order
replaced only on a tick change; tl30 counted from filled_at; 15:30 cancels
resting buys; EOD cancels owned OCOs before closing; ET day-roll resets meta
and cancels owned leftovers; $2 price floor + qty guard; terminal-status
classification; strict state required at the latest completed bar so the live
rank applies to the same minute.
Verified: py_compile + 5 mock-broker lifecycle tests (refresh-on-tick, expiry,
protect, tl30, exit bookkeeping) pass; live bot restarted (start live:true
05:12:29 ET, no error events).
Deliberate fidelity choices (not bugs): tick-only replacement; current-rank
alignment via latest-completed-bar state; cancel owned resting buys on restart.
Deferred: parity/ledger semantics (findings 17-24) to fix before trusting
reconciliation numbers.

## 2026-09-11k — parity/ledger semantics fixed (audit findings 17-24, partial)
Parity: reconstructed scans gated to rank<=3 (live gate); engine fills matched to
journal fills by nearest minute (<=5) instead of greedy first-match; artifact
records dmin plus an explicit "approx" list (one-bar scan timing and
POS_MAX/15:30/anchor guards NOT replicated).
Ledger: attribution now uses ET days on both sides (was UTC prefix -> late-day
fills would have been dropped as foreign); get_orders paginated; friction charged
once per round-trip on the closing leg; bids keyed by (ET day, symbol). Entry
cost remains weighted-average (not FIFO) — acceptable for v0 single-lot trades.
Regression: parity on the fabricated 2026-09-09 journal still reproduces the known
FTFT fill (tf=678, +10.11%); ledger baseline fills=0 / 54 foreign ignored.

## 2026-09-11l — LIVE BUG: SIP intraday sparse -> zero bids; IEX fix
- First live session check (09:44 ET): bot scanning every 60s, zero errors, but NO
  bids and no orders. Cause: broker.bars() returned on the first non-empty feed;
  SIP intraday on this plan returns only ~2 stale bars (13:30-13:31Z at 09:44)
  while IEX returns the full live session (16 bars by 09:45). With <16 bars the
  strict-state evaluator never fires -> zero state minutes -> zero bids.
- Fixed: flush_bot.bars() now tries IEX first (SIP fallback for gaps);
  forward_backfill_bars.fetch_day() uses IEX for the current ET day, SIP for
  history (tonight's parity depends on it).
- Restart 09:50:28 ET (live:true); 09:51 candidates all <+100% so no bids yet
  (TNON's brief +117% at 09:42 was missed during the SIP window; its scanner
  change later read +57.9% — TV-side reference shift, not ours).
- Watch midday for +100%-gain qualifying candidates; parity tonight uses IEX
  bars for today's session.

## 2026-09-11m — observer feed fix staged (IEX-first intraday)
forward_observe.py log_new_bars + deep_snapshot now try IEX before SIP (SIP
intraday returns ~2 stale rows on this plan). The edit is dormant: the running
observer keeps the old code until its next restart, deliberately deferred to
post-close to avoid duplicate promotion rows. The bot's equivalent fix is
already live (5f6dcc5).

## 2026-09-11n — live candidate source: Alpaca movers (TV scanner stale) + PIT/$2 filter
- Found live: the TV scanner returns stale/frozen rows for microcaps intraday
  (SWRD frozen at 3.56/+59.6% for 6+ min while the IEX last trade was 4.92 =>
  +120.6% vs the 2.23 prior close). The bot's change-based gain was therefore
  blind to a qualifying >=+100% name.
- Fix: scan_candidates() uses Alpaca's screener movers (live) as the primary
  source, TV as fallback; rows filtered to the latest PIT universe and the $2
  floor BEFORE ranks are assigned (raw movers are full of warrants/rights/units:
  APURR 0.39, CHPGR 0.15, AENTW 0.43, BRLSW 0.05, crowding the rank gate).
  Restarted live:true 10:10:26 ET; zero errors; 10:11 rows are clean PIT names
  (FTFT +45.8 r1, TNON +45.7 r2, ACVA +44.4 r3).
- No bids yet: no name has held causal gain >= +100% with fresh+thrust; SWRD's
  >=100% window (~09:5x-10:0x) was missed during the TV-blind period.

## 2026-09-11n — day-1 live verification: missed window accounted; IEX coverage caveat
- Verified end-to-end at ~10:15 ET: scan_candidates() sources Alpaca movers (PIT/$2
  filtered); BENF +123.65% rank1 but only 2 IEX bars (fresh listing/halt) -> cannot
  qualify (16-bar minimum, consistent with the r15 requirement).
- SWRD DID trade >=+100% intraday (33 IEX bars; max +103.8% session-relative,
  +120.6% vs its prior close at 09:54) — the first genuine qualifying opportunity
  was MISSED because the two feed bugs overlapped it: SIP-sparsity until 09:50
  (no bars) and stale-TV candidates until ~10:10 (SWRD showed +59.6%).
- IEX intraday coverage gaps observed (COLA/MKDW: 0 bars despite being top movers)
  => the live paper tape differs from the SIP historical tape; today's parity must
  use the IEX-based replay (forward_backfill_bars already patched).
- Journal place_bid count = 0; zero error events. Day-1 remains a no-trade session,
  which is rule-correct given the candidates and the tape available.

## 2026-09-11n — Alpaca /clock outage hardening (live)
- 12:16-13:02 ET: Alpaca Trading API returned 500 on /clock for ~46 consecutive
  polls -> the bot logged 44 error tracebacks and aborted each poll (it was flat,
  so no risk, but a live position would have been unmanaged during the outage).
- Fix: Broker.clock() retries once (1s) then falls back to the local ET session
  clock (weekday + 570<=minute<960); a single API hiccup no longer blinds the
  poll cycle. Restarted live:true via KILL after the edit.
- Also noted: one error event at 15:22 was an offline test invocation missing
  pyarrow (my shell), not the bot (the bot venv has pyarrow 25.0.1).

## 2026-09-11o — DAY-1 LIVE SESSION RECORD (zero fills)
- Bot live:true from 04:23 ET; the first session produced 0 bids/fills (no PIT
  name reached causal +100% with fresh+thrust while the fixed pipeline was live).
- Parity 2026-09-11 (IEX replay): 1369 bars, 14/14 syms, expected 0/actual 0.
  Ledger: fills=0, closed_trades=0. First live day closed flat.
- Live bugs found/fixed on day 1: SIP intraday sparse -> IEX-first (5f6dcc5);
  TV scanner stale -> Alpaca movers + PIT/$2 pre-rank filter (c45b07f); observer
  IEX fix activated post-close (bcd9b02; observer restarted 20:04Z); Alpaca
  /clock 500s -> retry + local-ET fallback (32dd271).
- Windows missed while feeds were broken: TNON +117% (09:42), SWRD ~+120%
  (09:54). Post-fix max mover ~+44%. Day 1 was pipeline validation, not edge
  evidence; accumulation continues next session.

## 2026-09-11p — offline verification: day-roll + clock fallback (pre-Monday)
Two live-critical paths had never been exercised: the ET day-roll (the bot
crosses midnight tonight and over the weekend) and the new /clock fallback.
Added T6 (day-roll: stale meta + owned resting buy + open position -> cancel,
close, meta reset to today, day_roll logged) and T7 (clock: retry once then
local-ET fallback; a transient failure retries without fallback) to the mock
harness. All 7 tests pass; harness preserved at factory/scripts/test_flush_bot.py.

## 2026-09-11p — post-close scan gating (cosmetic noise fix)
poll() scanned before the market-open gate, so movers/bars calls and scan_rows
continued after the close (125 rows after 15:55 on day 1; no orders possible —
entry cutoff 15:30 + EOD return). Scans now run only when the broker clock
reports open (or --probe); closed sessions emit market_closed and stay quiet.
Restarted live:true for the weekend.

## 2026-09-11q — HANDOFF.md written (project root)
Comprehensive prompt-style handoff for the next agent at HANDOFF.md: frozen-rule
spec, evidence base (OOS +1.14%/trade pf>=2, 12/14; execution realism; decay
watch), live systems inventory, environment commands (KILL restart, cmd.exe
quoting, uv patterns), Alpaca/WSL landmines, research discipline, immediate
roadmap (Monday session -> 30+ fills -> tiny sizing), do-not list, and explicit
context-discipline instructions: compress big and often.

## 2026-09-12 — execution-semantics audit + v2.1 fixes (market closed)
Advisor's 8-item audit triaged against code; 5 material fixes applied to
`factory/scripts/flush_bot.py`, all mock-tested (T1-T10, 11/11 pass). Frozen
alpha untouched; only the fidelity of the live translation changed.

Fixed:
- Parity r15: `state_minutes()` now evaluates r15 on a forward-filled 1-minute
  grid, matching the research tape (`lb18.py:97-103`). Previous `shift(15)` on
  raw IEX rows meant 15 *printed* bars, which on sparse IEX spans far longer
  than 15 clock minutes and mislabels thrust.
- Parity tl30: time-stop is now 30 clock minutes from `filled_at` (30 new bars
  on the near-complete tape); a bare IEX bar count over-held.
- Partial fills: `sync_fills()` inspects working orders each poll (previously
  skipped while still in the open book), cancels the remainder, books the
  broker-reported fill, and protects held qty; `manage_symbol()` resizes the
  OCO if the remainder filled before the cancel landed (protect_resize).
- Restart: `startup_reconcile()` cancels owned resting entry buys, re-adopts
  broker positions, and rehydrates entry_B/entry_c0/entry_ts from today's
  journal (+broker filled_at) so protection/tl30 keep the original anchor.
- KILL: now intentional — cancel owned entry buys, leave protective OCO sells
  in place, warn on unprotected positions, exit. No auto-flatten. Idempotent
  on supervisor relaunch.
- pf_est: journal tag on `place_bid`/`fill` only (causal prior-flush count,
  IEX-undercounted). Not an entry gate: paper deliberately trades the broad
  population; fills partition ex-post into pf0/pf1/pf>=2. Frozen result
  preserved; a corrected pf definition stays a separate research variant.

Not changed (assessed, intentional): fill detection = broker truth (no sim);
scanner already Alpaca-movers + PIT/$2; ownership = dedicated paper account
(now stated in the module docstring); micro `fc<0` label is "filled at/below
bid", a different concept from the execution study's "gap-through" (see
HANDOFF §13 terminology note) — a research-labeling clarification, not a bot bug.

Pending: production-overlay measurement (fill cutoff 15:30 / flatten 15:55 /
POS_MAX=3 on historical fills -> retained EV + frequency) delegated as a
measurement (seen data, not an alpha claim); artifact `lb18_overlay.json`.
Next: Monday pre-open health check, then accumulate paper fills tagged pf_est.

## 2026-09-12r — Study A (IEX feed fidelity) + Amendment A1
PRE-REG-MICRO-01 Study A: replayed the frozen rolling-bid engine on an IEX-only
tape built for all candidate symbol-days (667 days cached, 170 zero-bar pairs
logged in `data/iex_tape/_missing.jsonl`). Parity reproduced the frozen numbers
exactly (all 541/+0.91%; pf2 381/+1.14%; dev 937/+0.39%, 658/+1.20%), so the
substitution machinery is sound. The IEX-everything arm FAILED Gate A: OOS all
n=456 −2.54%, pf2 n=256 −1.56% (3/14); dev pf2 −1.25%. Failure signature is
asymmetric: the 174 missed frozen fills (118 pf2) were target winners (median
frozen ret +10.11%) and the 89 spurious IEX fills were stop losers (median
−11.00%). IEX-only fill detection is adversely selected; live fills are
consolidated (real broker order), so this arm is stricter than live. Amendment
A1 (pre-registered before running) decomposes into (IEX state, frozen exec) =
deployable hybrid, (frozen state, IEX exec) = fill-venue control, plus an
IEX-anchor/frozen-B sub-variant, each with frozen gates and a decision rule
(A3 PASS ⇒ state feed not implicated). Files: `factory/scripts/lb18_iex_tape.py`,
`lb18_iex_feed.py`, `factory/artifacts/lb18_iex_feed.json`/.parquet. Verdict
pending the hybrid run.

## 2026-09-12u — Study B (post-fill tail rescue) + independent verification
Study B's gate technically PASSES; my replication of the raw rows sharpens what is
real and what is selection noise.

Artifacts: `factory/scripts/lb18_subminute.py`, `factory/artifacts/lb18_subminute.json`
/`.parquet` (541 fills x 6 configs = 3246 rows; SIP anchors 499/541; 2 truncated
trade windows, 0 errors). Counterfactual exits are friction-consistent
(`exit = action_price/B - 1 - 0.01`, same 1% as the baseline).

Agent's best seen-data rules: all Δ=5s+60s EV +0.91% -> +1.34%; pf2 Δ=30s+60s
+1.14% -> +1.73% vs clean-touch +1.39%. But 25,272 rules were searched on the
same 14 OOS months; winning-rule identity reshuffles completely across adjacent
Δ/lag (unrelated features and directions) and the all-population winner improves
only 5/14 months.

Independent verification (mine, from the per-fill parquet):
- 7-month train / 7-month test re-selection: lag=0 transfers positive in only
  1/12 configs; lag=60 in 11/12.
- leave-one-month-out (select on 13 months, apply to the held-out month):
  lag=0 negative/weak (−0.41..+0.26pp); lag=60 positive in 8/8 configs
  (all +0.09..+0.50pp; pf2 +0.47..+1.00pp). pf2 Δ=15s+60s = +0.997pp, 12/14
  months. Rule identity stays unstable (9-14 distinct rules/fold) ⇒ the value is
  in the feature FAMILY (participation size/volume at B, dwell/below-span, max
  depth below B, snapback at ~60s), not one rule.
- unconditional action on EVERY fill: strongly negative at lag=0 (−1.2..−1.8pp),
  POSITIVE at lag=60 (all +0.44..+0.66pp; pf2 +0.74..+0.82pp, 10/14 months,
  worst month −0.55% vs −2.03%, std 7.2 vs ~10). The simple "exit ~60s after
  first touch" knob captures most of the gate; the 25k-rule search adds little.
- Mechanism: among fills still alive at the action point, later-stops outnumber
  later-targets ~1.8:1, so a flat early exit wins on base rates. Instant action
  is noise; ~60s is where the conditional odds turn.

Verdict: measurement only, NOT adoptable. Blockers: (1) exit execution is
optimistic — it fills at the last SIP print with the same flat 1% friction as
the baseline, while real market-exit slippage on these names is the known killer
(gap-through −7.64%); (2) the 14 OOS months are now seen for this exit question,
so any 60s-exit rule needs a NEW pre-reg + fresh/forward OOS. Next experiment
(pre-reg first): unconditional exit-time sweep with a conservative exit-slippage
model and a small candidate set (flat time-stop; "exit if depth below B < −1%"),
then forward paper validation.

## 2026-09-12v — live-observable data surface (deployability inventory)
Checked what `flush_bot.py` can actually see live (from code, not assumption):
- `Broker.bars()` = `DataFeed.IEX` first, SIP fallback (lines 307-319) → 1-min IEX
  OHLCV, polled every `POLL_S=60`.
- `Broker.trades_at_bid()` = `DataFeed.SIP` (lines 331-337) → returns nothing live
  (SIP intraday blocked on this key); harmless, used for post-fill micro only.
- No quotes/NBBO fetch anywhere in the bot.
Consequence for rescue rules: every Study B sub-minute feature
(touch_trade_count/volume/size_median, below_span_s, touch_episodes,
max_depth_below, snapback at 5-30s) is NOT live-observable; only fill-time and
IEX 1-min bar features (e.g. `iex_above_B`) are.
- R1 (flat time-stop at H) is deployable today with zero new data.
- Any feature-based rescue needs either an IEX websocket trades/quotes consumer
  (code only, no purchase) or real-time SIP (Algo Trader Plus, ~$99/mo).
- `trades_at_bid` still passes `limit=10000` (the old total-cap pattern); moot
  live, but fix it if it is ever pointed at historical fetches.

## 2026-09-12w — PRE-REG-EXIT-01: flat time-stop DEAD; keep the frozen exit
Conservative exit test on the 541 frozen OOS fills: exit priced at the NBBO
**bid** (not the last print) plus 50bps extra slippage. Result: every flat or
conditional time-stop fails — the Study B "gate PASS" was exit-pricing optimism
(last SIP trade + flat 1%).
- At the touch instant the bid is already below B: mean `bid_H/B` 0.967, median
  0.992, p05 0.866. Filling a flush and then selling at the market means hitting
  a bid below entry, worst on exactly the names that keep falling.
- EV (SLIP=50bps): R1 flat H=0 −3.77%, H=60 −3.93%, H=1800 −4.12%; R2 conditional
  (exit iff price<B*0.99) −3.77%..−4.90%. Months+ 0-1/14. Frozen baseline on the
  same fills: +0.914% (all) / +1.136% (pf2).
- Mechanism: the edge lives in the RESTING limit target at c0 (48% of fills exit
  at +10.11% net). A time-stop sells the winners into a falling bid; it sacrifices
  ~112% of aggregate target P&L. Survivors are stop-heavy (~1.8:1) yet exiting
  them costs more than the stops saved once priced at the bid.
- Coverage: 499/541 anchored, 0 quote errors/truncations; 42 anchor-missing; 88
  trade fallbacks (mostly H=1800). A3b secondary not run (cache 361/507).
VERDICT: post-fill time-stop lane **CLOSED** (the Study B deprioritization rule
fires — no rung beats the baseline). Keep the frozen resting exit unchanged. No
SIP needed for this question. Files: `factory/scripts/lb18_exit.py`,
`factory/artifacts/lb18_exit.json`/.parquet, `researches/PRE-REG-EXIT-01.md`.

## 2026-09-12s — Study A decomposition (Amendment A1): IEX state costs frequency, not edge
2×2 over (state/anchor source) × (exec source), frozen `lb` gate, `lb18_iex_hybrid.py`:
parity (frozen,frozen) reproduced 541/+0.91%, 381/+1.14% exactly.

| variant | pf2 n | pf2 EV | months+ | missed_pf2 | note |
|---|---|---|---|---|---|
| frozen | 381 | +1.14% | 12/14 | 0 | reference |
| **A3b IEX state+B, frozen exec** | **282** | **+1.13%** | **10/14** | **77 (20.2%)** | deployable live model |
| A3a IEX state, frozen B, frozen exec | 267 | +1.59% | 9/14 | 64 (16.8%) | anchor/state only |
| A4 frozen state+B, IEX exec | 335 | −3.03% | 1/14 | 89 (23.4%) | fill-venue control |

Verdict: A4 confirms IEX-only *fill detection* drove Study A's FAIL (catastrophic,
and not the live model — live fills are consolidated). For the deployable model
A3b, **per-trade EV is preserved (+1.13% vs +1.14%)** but ~20% of frozen pf2
fills are not captured, and month stability drops to 10/14 (worst −3.09%).
The loss is dominated by the IEX state/anchor schedule (anchor_lost 31 +
lifecycle_lost 36 + fill_lost 10 pf2), not by B: A3a (frozen B) recovers 13 pf2
fills (EV +1.59%) but is *less* month-stable (9/14). So the IEX B/c0 drift is
minor; the sparser IEX tape simply arms fewer anchors. IEX `prior_flush` also
undercounts: variant pf2 n=282 vs 304 frozen-pf2 matched pairs ⇒ the live pf_est
tag undercounts true-pf2 fills by ~7%. Matched-fill paired delta (frozen−IEX
ret) n=367 mean +2.30% p95 +21.1% ⇒ same-fill exit paths differ enough to flip
target→stop in a chunk of cases.

Consequence: judge the live paper bot against the A3b baseline (**pf2 ≈ +1.13%,
10/14, ~19 fills/mo post-overlay, worst month ≈ −3.1%**), NOT the frozen
reference. Real-time SIP (Algo Trader Plus ~$99/mo) is the only way to restore
the frozen schedule; a separate priced decision, not adopted. Study B (tail
management on the frozen population) remains valid and needs no subscription.

## 2026-09-12t — fragility profile of the live flush edge (descriptive, seen data)
A3b pf2 (the deployable live model), OOS 2024-01..2025-02, from lb18_iex_hybrid.parquet:
win 55.0%, mean win +9.24%, mean loss −8.77%, payoff 1.05, breakeven win-rate
48.7% => cushion ~6.3pp. Exit mix: 48% exactly target (+10.1% net), 28% exactly
stop (−11%), 23% tl30/other. Bootstrap 95% CI of mean [+0.01%, +2.21%] (frozen
[+0.18%, +2.09%]) — lower bound touches zero; ~14 months of forward fills needed
to exclude zero at ~19/mo. Worst 5 fills −75.9pp of +318pp total; ex-worst5 mean
+0.97%. H1 +2.18% (n=130) -> H2 +0.23% (n=152): recent half flat. $/mo ≈ $422 at
$2k/trade, $2.1k at $10k, $5.3k at $25k (5%-participation capacity p25 ≈ $84k).
Implication: thin, fat-tailed, decaying edge; Study B (tail) is the highest-
leverage lever; paper fills validate mechanics, not the edge; no more untouched
data remains for the frozen rule (forward is the only true OOS).

## 2026-09-12x — Pattern digest phase 1 (PRE-REG-PATTERN-01 v1)
Unsupervised top-3 digest (user directive: top-3 only, every minute, no label).
Corpus: data/leaderboard minute grid, 2,133,120 60-min windows over 667 days /
7015 symbol-days. Frozen config L=60 k=16: silhouette 0.0058, occupancy entropy
0.886, one 95-window cluster -> the flattened-Euclidean representation does NOT
separate path shapes; clusters degenerate into time-of-day and level slices
(`minute_index` was a clustering channel). Only C12 shows a recognizable
ramp-then-plateau (51% rank-1, median 60-min +38.8%). Month occupancy TV 0.062.
IEX coverage (informs the SIP decision): 172/7015 top-3 pairs have zero IEX bars
all day; median per-pair IEX session-minute share 24.6% (pooled 33.9%) -> an
IEX-only live feed is materially sparse. No outcomes or targets used anywhere.
Amendment A1 freezes v2 (time-of-day excluded, per-window z-scored shape) with a
stopping rule: best silhouette < 0.05 => representation class exhausted.
Artifacts: factory/scripts/pattern_digest.py,
factory/artifacts/pattern_digest.json/.parquet/_cards.png. EXP-67.

## 2026-09-12y — Pattern digest v2 (Amendment A1): EXHAUSTED
Per-window z-scored five-channel shape clustering (time-of-day removed from the
vector). Silhouettes: k=8 0.0206 (best/selected), k=16 -0.0358, k=24 -0.1452.
Best < 0.05 => the frozen stopping rule declares the flattened-Euclidean shape
class EXHAUSTED; no further variants/k were run. The z-scored envelopes render
as interpretable archetypes (C0 spike-and-fade, C1 broad-V, C5
ramp-to-plateau, C7 plateau-decline; C4/C6 near-duplicate late ramps), but the
partition does not separate: silhouette ~0.02 means within-cluster variance
dominates 300-D flattened distance. Interpretable k-means medians are NOT
evidence of recoverable structure — do not read these as patterns unless the
Scoring pass (still unrun, user-gated) says otherwise. Month stability TV
0.0168; 2,133,120 assignments; one degenerate cluster (n=3). No outcomes or
labels used. Artifacts: pattern_digest_v2.json/.parquet/_cards.png (producer
pattern_digest.py --variant v2). EXP-68. Lane status: unsupervised shape
discovery at 1-min/60-min granularity on the top-3 population is a tested
negative; continuation needs a separate pre-reg with a different representation
(learned embedding) or unit of analysis (event-anchored windows).

## 2026-09-12z — live-path audit: pyarrow dependency gap in the supervisor gate
Journal audit of day 1: 46 error events = 45 `/clock` 500s (the known Alpaca
outage 12:16-13:02 ET, handled by retry + local-ET fallback) + 1 real live-path
failure at 15:22:17 ET: `alpaca_movers` raised "pyarrow is required for parquet
support" while reading `data/pit/pit_symbols.parquet` (flush_bot.py:136-137), so
that movers poll produced no candidates. pyarrow (25.0.1) is present in the
hermes venv now and the read works (3,944,234 rows / 703 vintages / 1.72s); the
failure window was environment drift, not code. The supervisor's dependency gate
checked loguru/pandas/dotenv/alpaca but NOT pyarrow, so a missing parquet engine
would let the bot start and silently degrade candidate scanning. Gate now
includes pyarrow (activates on the next supervisor start; the running process is
untouched). Bot otherwise healthy: LIVE armed, no KILL, startup_reconcile ran
(canceled 0 / rehydrated 0), weekend-idle on market_closed.

## 2026-09-13 — PRE-REG-DAYTYPE-01: day-context candidate (OOS pass, dev caveat)
Five pre-declared causal day/session cuts over 1478 frozen fills (OOS 541 +
dev 937), day aggregates built from data/leaderboard (strict-state names,
flush names; producer `factory/scripts/lb18_daytype.py`). Result: the day-breadth
cut `n_strict_names_sofar >= 2` (distinct strict-state top-3 names already seen
strictly before the fill minute) formally PASSES the frozen 5-condition gate on
OOS: mean +0.46% (n=207) vs +1.20% (n=334) for <=1; rank1-only -0.69% vs
+1.33%; rank1&pf2 -1.41% vs +1.57%; below population in 3/3 OOS blocks
(5/5/4 months); dose-response monotone (rank1 OOS: 1 → +1.33%, 2 → -0.49%,
3 → -0.04%, 4+ → -5.79% n=7); session-independent. Caveat: NOT replicated
within rank1 on dev (1:+0.98%, 2:+1.22%, 3:+0.26%) although pooled dev agrees
(+0.93% vs -0.16%). Other four cuts fail the gate (n_flush quiet days n=19;
rank2-3 negative OOS but dev opposite; prior_stop_today ~flat; session
direction agrees but gate fails). Falsified the convenient composition story:
share2+ H1 0.422 vs H2 0.457; corr(monthly share2+, monthly mean) -0.08 dev,
+0.01 OOS — the H2 fade is NOT day-composition. No adoption: H029 = CANDIDATE,
needs forward paper validation. Next: journal `n_strict_est` in flush_bot.py
(IEX-estimated day breadth at bid/fill, behavior-neutral tag) so Monday+ fills
test the candidate in the only uncontaminated arena. EXP-69.

## 2026-09-13b — H029 robustness (Amendment A1): downgraded to OOS-only
Six frozen robustness checks on the day-breadth candidate. R1 leave-one-month-out:
14/14 OOS folds positive (+0.50..+1.22pp). R5: the difference survives removing
the top-3 (date,ticker) contributors (+0.44pp). R3: the A3b deployable population
agrees in direction and larger (quiet +1.29% n=315 vs busy -1.30% n=192).
BUT R2: day-clustered bootstrap 95% CI = [-1.14%, +2.42%] pooled and
[-0.06%, +4.02%] rank1 — both include zero; R6 permutation p=0.194 pooled
(0.026 rank1); R4's alternative breadth definition has almost no variation
(OOS quiet n=13) and is inconclusive. Verdict per the frozen rule: H029 is an
OOS-only association, NOT robust — no gating, no adoption. The live bot's
behavior-neutral `n_strict_est` tag stays for forward paper comparison.
Artifacts: lb18_daytype_robust.py/.json. EXP-70, H029r.

## 2026-09-13c — Pattern digest phase 2 (event-anchored): shape family closed
PRE-REG-PATTERN-02 frozen agent-side under the standing goal directive; producer
factory/scripts/pattern_digest_events.py; 668 day caches under
data/scratch_pattern_events (26MB, gitignored). Results:
- E1 flush touch: 14,116 / 9,988 / 6,586 windows (L 15/30/60) over 4,560
  symbol-days; best silhouette 0.0411 (L=15,k=8) < 0.05 => EXHAUSTED.
- E2 thrust: 1,783 / 1,155 / 750 windows over 714 symbol-days; largest < 2,000
  power floor => INCONCLUSIVE_POWER (not tested).
- E3 volume spike: 3,479 / 2,908 / 2,107 windows over 1,232 symbol-days; best
  0.0433 (L=15,k=8) < 0.05 => EXHAUSTED; occupancy drifts across months (TV 0.123).
Cards (E1, E3) show a few different medians (hump-fade, fade, hump-reversal) but
heavy interquartile overlap; as with v1/v2, interpretable medians are not
recoverable structure. No outcomes or labels anywhere; the Sec.6 scoring pass
remains unrun and user-gated. Lane status: all shape-clustering units
(all-minutes v1/v2, event-anchored E1/E3) are tested negatives; the only
remaining branch is a learned sequence embedding under its own pre-reg.
Artifacts: pattern_digest_events.json/_assignments.parquet/
_cards_E1_flush_touch.png/_cards_E3_volume_spike.png. EXP-71, H030.

## 2026-09-13d — ledger tag partitions for the forward-paper judge
flush_bot_ledger.py now attaches `pf_est` / `n_strict_est` to each round trip
(read from place_bid/fill journal events) and prints `summary_by_tag`
partitions (pf 0-1 vs 2+, n_strict 0-1 vs 2+) plus the A3b baseline
(+1.13% pf2 mean net) for the forward comparison. Smoke test on the flat
account: fills=0 closed_trades=0, artifact written.

## 2026-09-13e — Vacuum-event census (PRE-REG-CENSUS-01): rank gradient + frequency headroom
Measurement only, no trading rule. 24,655 vacuum events over 667 days (36.9/day).
Key numbers: rank1 7.47 events/day, recovery-to-session-max in 30m 41.4%, median
3m; rank2 5.74/29.3%/5m; rank3 4.37/24.0%/5m; off-top-3 19.33/13.0%/11m.
Event-level prior_flush is INVERSE (pf0 32.0%, pf1 23.4%, pf2+ 19.4%) and the
pf2+ pool is dominated by off-rank declining names (off|2+ n=7466, 8.8%) — so
H025's pf>=2 edge is a name-level survivor/state conjunction, not an
event-sequence effect. Halt-adjacent vacuums (pre-gap>=5m) recover more often
(36.2%) with fatter tails (p05 -23.8%) but only 2.48/day. AM strongly better
than PM (29.9% vs 13.7%). Frequency headroom: H025 executes ~1.9 fills/day vs
7.5 rank1 vacuum events/day — the entry qualification, not the phenomenon, is
the constraint on dollars. Frozen gate licenses (candidate only): rank1/2/3,
pf0/1, seq1/2, AM, rank1|pf2+ (5.52/day, 38.0%), rank2|pf2+, off|0, pf0|seq1,
pf1|seq2. No rule adopted; H025 and the live bot untouched.
Artifacts: factory/scripts/event_census.py, factory/artifacts/event_census.json/.parquet
(cache data/scratch_census/). EXP-72, H031.
